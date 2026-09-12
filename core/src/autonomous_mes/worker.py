import argparse
import logging
from uuid import uuid4

from autonomous_mes.config import Settings
from autonomous_mes.infrastructure.database import build_engine, build_session_factory
from autonomous_mes.infrastructure.outbox_worker import (
    LoggingEventPublisher,
    SqlAlchemyOutboxWorker,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish pending manufacturing events")
    parser.add_argument("--batch-size", type=int, default=50)
    args = parser.parse_args()

    settings = Settings()
    if settings.storage_backend != "postgresql":
        parser.error("outbox worker requires AUTONOMOUS_MES_STORAGE_BACKEND=postgresql")
    logging.basicConfig(level=settings.log_level)
    engine = build_engine(settings.database_url)
    worker = SqlAlchemyOutboxWorker(
        build_session_factory(engine),
        LoggingEventPublisher(),
        worker_id=f"worker-{uuid4()}",
    )
    return 0 if worker.run_once(args.batch_size) >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
