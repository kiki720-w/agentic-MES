import argparse
import json
import math
import statistics
import time
from collections.abc import Sequence
from urllib.parse import urlsplit

import psycopg
from psycopg import sql

DEFAULT_URL = "postgresql+psycopg://mes@127.0.0.1:55432/agentic_mes"
DEFAULT_SCHEMA = "agentic_mes_scale"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an isolated CAPAXION scale dataset")
    parser.add_argument("--database-url", default=DEFAULT_URL)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--work-orders", type=int, default=100_000)
    parser.add_argument("--events", type=int, default=1_000_000)
    parser.add_argument("--samples", type=int, default=30)
    return parser.parse_args()


def psycopg_url(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def validate_scale_target(value: str, schema_name: str) -> None:
    parsed = urlsplit(psycopg_url(value))
    database_name = parsed.path.lstrip("/")
    if database_name != "agentic_mes" or not schema_name.startswith("agentic_mes_scale"):
        raise SystemExit(
            "refusing to run outside database agentic_mes and an agentic_mes_scale* schema"
        )


def ensure_schema(
    connection: psycopg.Connection[tuple[object, ...]], schema_name: str
) -> None:
    identifier = sql.Identifier(schema_name)
    connection.execute(sql.SQL("create schema if not exists {}").format(identifier))
    connection.execute(
        sql.SQL(
            "create table if not exists {}.work_orders "
            "(like public.work_orders including all)"
        ).format(identifier)
    )
    connection.execute(
        sql.SQL(
            "create table if not exists {}.event_outbox "
            "(like public.event_outbox including all)"
        ).format(identifier)
    )
    connection.execute(
        sql.SQL(
            "create index if not exists {} on {}.event_outbox (occurred_at, event_id)"
        ).format(sql.Identifier("ix_scale_outbox_timeline"), identifier)
    )
    connection.execute(
        sql.SQL("set search_path to {}, public").format(identifier)
    )
    connection.commit()
    print(f"using isolated schema {schema_name}", flush=True)


def seed(connection: psycopg.Connection[tuple[object, ...]], work_orders: int, events: int) -> None:
    current_orders, current_events = connection.execute(
        "select (select count(*) from work_orders), (select count(*) from event_outbox)"
    ).fetchone()
    if current_orders or current_events:
        if current_orders == work_orders and current_events == events:
            print("scale dataset already exists; reusing it", flush=True)
            return
        raise SystemExit(
            "scale database is not empty and has different row counts; use a new suffixed scale database"
        )

    print(f"inserting {work_orders:,} work orders", flush=True)
    connection.execute("set synchronous_commit = off")
    connection.execute(
        """
        insert into work_orders (
            work_order_id, human_code, production_order_id, workshop_id, quantity,
            due_at, priority, product_revision_id, routing_revision_id, bom_revision_id,
            drawing_revision_ids, operations, status, version, created_at, updated_at
        )
        select
            'scale-wo-' || lpad(i::text, 27, '0'),
            'WO-SCALE-' || lpad(i::text, 6, '0'),
            'PO-SCALE-' || lpad(i::text, 6, '0'),
            'WS-SCALE-' || ((i - 1) %% 5 + 1),
            ((i - 1) %% 100 + 1),
            now() + (((i - 1) %% 30) || ' days')::interval,
            ((i - 1) %% 100 + 1),
            'PR-SCALE-R1', 'RT-SCALE-R1', 'BOM-SCALE-R1',
            '["DWG-SCALE-R1"]'::jsonb,
            jsonb_build_array(jsonb_build_object(
                'sequence', 10,
                'operationCode', 'TURN',
                'operationName', '数控车削',
                'workCenterId', 'WC-SCALE-1',
                'plannedQuantity', ((i - 1) %% 100 + 1),
                'goodQuantity', 0,
                'scrapQuantity', 0,
                'status', 'PENDING',
                'assignedResourceId', null
            )),
            (array['DRAFT','RELEASED','IN_PROGRESS','SUSPENDED','COMPLETED'])[((i - 1) %% 5 + 1)],
            1,
            now() - ((%s - i) || ' seconds')::interval,
            now() - ((%s - i) || ' seconds')::interval
        from generate_series(1, %s) as generated(i)
        """,
        (work_orders, work_orders, work_orders),
    )
    connection.commit()

    print(f"inserting {events:,} manufacturing events", flush=True)
    connection.execute("set synchronous_commit = off")
    connection.execute(
        """
        insert into event_outbox (
            event_id, event_type, schema_version, aggregate_type, aggregate_id,
            occurred_at, correlation_id, causation_id, payload, publish_status, attempts
        )
        select
            'scale-evt-' || lpad(i::text, 26, '0'),
            (array['WorkOrderCreated','OperationStarted','ProductionReported','QualityRecorded'])[((i - 1) %% 4 + 1)],
            '1.0',
            'WorkOrder',
            'scale-wo-' || lpad((((i - 1) %% %s) + 1)::text, 27, '0'),
            now() - ((%s - i) || ' milliseconds')::interval,
            null, null,
            jsonb_build_object('scaleSequence', i),
            case when i %% 20 = 0 then 'PENDING' else 'PUBLISHED' end,
            0
        from generate_series(1, %s) as generated(i)
        """,
        (work_orders, events, events),
    )
    connection.commit()
    connection.execute("analyze work_orders")
    connection.execute("analyze event_outbox")
    connection.commit()


def percentile(samples: Sequence[float], percentile_value: float) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(percentile_value * len(ordered)) - 1)
    return ordered[index]


def measure(
    connection: psycopg.Connection[tuple[object, ...]],
    statement: str,
    parameters: tuple[object, ...],
    samples: int,
) -> dict[str, float]:
    for _ in range(3):
        connection.execute(statement, parameters).fetchall()
    durations: list[float] = []
    for _ in range(samples):
        started = time.perf_counter()
        connection.execute(statement, parameters).fetchall()
        durations.append((time.perf_counter() - started) * 1000)
    return {
        "p50Ms": round(statistics.median(durations), 3),
        "p95Ms": round(percentile(durations, 0.95), 3),
        "maxMs": round(max(durations), 3),
    }


def benchmark(
    connection: psycopg.Connection[tuple[object, ...]], work_orders: int, samples: int
) -> dict[str, object]:
    deep_order_offset = max(0, work_orders - 100)
    event_count = int(connection.execute("select count(*) from event_outbox").fetchone()[0])
    deep_event_offset = max(0, event_count - 100)
    event_cursor = connection.execute(
        "select occurred_at, event_id from event_outbox "
        "order by occurred_at desc, event_id desc offset %s limit 1",
        (deep_event_offset,),
    ).fetchone()
    if event_cursor is None:
        raise RuntimeError("event cursor could not be resolved")
    cases = {
        "workOrdersFirstPage": (
            "select * from work_orders order by updated_at desc, work_order_id desc limit 30",
            (),
        ),
        "workOrdersDeepOffset": (
            "select * from work_orders order by updated_at desc, work_order_id desc offset %s limit 30",
            (deep_order_offset,),
        ),
        "workOrdersStatusPage": (
            "select * from work_orders where status = 'SUSPENDED' order by updated_at desc limit 30",
            (),
        ),
        "workOrdersCodeSearch": (
            "select * from work_orders where human_code ilike %s order by updated_at desc limit 30",
            ("%999%",),
        ),
        "workOrdersSummary": (
            "select status, count(*) from work_orders group by status",
            (),
        ),
        "eventsRecent": (
            "select * from event_outbox order by occurred_at desc, event_id desc limit 100",
            (),
        ),
        "eventsDeepOffset": (
            "select * from event_outbox order by occurred_at desc, event_id desc offset %s limit 100",
            (deep_event_offset,),
        ),
        "eventsDeepCursor": (
            (
                "select * from event_outbox where (occurred_at, event_id) < (%s, %s) "
                "order by occurred_at desc, event_id desc limit 100"
            ),
            (event_cursor[0], event_cursor[1]),
        ),
    }
    results: dict[str, object] = {}
    for name, (statement, parameters) in cases.items():
        print(f"benchmarking {name}", flush=True)
        results[name] = measure(connection, statement, parameters, samples)
    return {
        "workOrders": work_orders,
        "events": event_count,
        "samplesPerCase": samples,
        "results": results,
    }


def main() -> None:
    args = parse_args()
    if args.work_orders < 1 or args.events < 1 or args.samples < 5:
        raise SystemExit("work-orders/events must be positive and samples must be at least 5")
    validate_scale_target(args.database_url, args.schema)
    with psycopg.connect(psycopg_url(args.database_url)) as connection:
        ensure_schema(connection, args.schema)
        seed(connection, args.work_orders, args.events)
        result = benchmark(connection, args.work_orders, args.samples)
    print("SCALE_BENCHMARK_RESULT=" + json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
