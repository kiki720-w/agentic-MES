import argparse
import logging
import time
from uuid import uuid4

from autonomous_mes.application.event_consumers import (
    QualityRecommendationEventPublisher,
    SchedulingAgentEventPublisher,
)
from autonomous_mes.application.outbox import EventPublisher
from autonomous_mes.config import Settings
from autonomous_mes.infrastructure.database import build_engine, build_session_factory
from autonomous_mes.infrastructure.outbox_worker import (
    LoggingEventPublisher,
    SqlAlchemyOutboxWorker,
)
from autonomous_mes.infrastructure.sqlalchemy_store import SqlAlchemyWorkOrderStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish pending manufacturing events")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument(
        "--quality-agent-auto-draft",
        action="store_true",
        help="generate R2 quality recommendation drafts from OperationCompleted events",
    )
    parser.add_argument(
        "--scheduling-agent-auto-plan",
        action="store_true",
        help="run the bounded L3 scheduling Agent after relevant manufacturing events",
    )
    parser.add_argument("--watch", action="store_true", help="continue polling for new events")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()

    settings = Settings()
    if settings.storage_backend != "postgresql":
        parser.error("outbox worker requires AUTONOMOUS_MES_STORAGE_BACKEND=postgresql")
    logging.basicConfig(level=settings.log_level)
    engine = build_engine(settings.database_url)
    sessions = build_session_factory(engine)
    publisher: EventPublisher = LoggingEventPublisher()
    if args.quality_agent_auto_draft:
        publisher = QualityRecommendationEventPublisher(
            SqlAlchemyWorkOrderStore(sessions), publisher
        )
    if args.scheduling_agent_auto_plan:
        if not settings.scheduling_agent_enabled:
            parser.error("scheduling Agent is disabled by configuration")
        workshop_ids = tuple(
            item.strip()
            for item in settings.scheduling_agent_workshop_ids.split(",")
            if item.strip()
        )
        if not workshop_ids:
            parser.error("scheduling Agent requires at least one configured workshop")
        publisher = SchedulingAgentEventPublisher(
            SqlAlchemyWorkOrderStore(sessions),
            publisher,
            workshop_ids=workshop_ids,
            horizon_days=settings.scheduling_agent_horizon_days,
            default_minutes_per_unit=settings.scheduling_agent_default_minutes_per_unit,
            use_overtime=settings.scheduling_agent_use_overtime,
            auto_submit=settings.scheduling_agent_auto_submit,
        )
    worker = SqlAlchemyOutboxWorker(
        sessions,
        publisher,
        worker_id=f"worker-{uuid4()}",
    )
    if not args.watch:
        return 0 if worker.run_once(args.batch_size) >= 0 else 1
    if not 0.1 <= args.poll_seconds <= 60:
        parser.error("--poll-seconds must be between 0.1 and 60")
    try:
        while True:
            processed = worker.run_once(args.batch_size)
            if processed == 0:
                time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
