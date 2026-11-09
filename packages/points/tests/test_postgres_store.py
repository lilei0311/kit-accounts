"""PostgreSQL integration tests for durable points + request lifecycle.

These require a local disposable Postgres. They are skipped when the server
is unavailable. They never connect to NAS / production.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

import pytest

from maxkit_points import IdempotencyConflict, PointsEngine
from maxkit_points.postgres import PostgresPointsStore
from maxkit_usage_bridge import PointsUsageSink, RateTable, UsageEvent

pytestmark = pytest.mark.postgres


def _text(*, input_units: int = 0, output_units: int = 10_000) -> UsageEvent:
    return UsageEvent(
        model_id="demo-text-v0",
        modality="text",
        units=None,
        unit_kind="tokens",
        input_units=input_units,
        output_units=output_units,
    )


def test_restart_preserves_balances_and_request_state(pg_store: PostgresPointsStore):
    dsn = pg_store._conninfo
    schema = pg_store.schema
    engine = PointsEngine(store=pg_store, clock=lambda: datetime(2026, 11, 9, tzinfo=UTC))
    engine.apply_grant(owner_id="acct-1", amount=1000, key="g1")
    sink = PointsUsageSink(engine, RateTable.from_snapshot())
    held = sink.hold(
        _text(),
        account_id="acct-1",
        request_id="req-restart",
        billing="platform",
    )
    assert held.status == "held"
    assert held.points == 8
    available = engine.available("acct-1")
    pg_store.close()

    store2 = PostgresPointsStore(dsn, schema=schema)
    try:
        engine2 = PointsEngine(store=store2, clock=lambda: datetime(2026, 11, 9, tzinfo=UTC))
        sink2 = PointsUsageSink(engine2, RateTable.from_snapshot())
        assert engine2.available("acct-1") == available
        replay = sink2.hold(
            _text(),
            account_id="acct-1",
            request_id="req-restart",
            billing="platform",
        )
        assert replay.status == "held"
        assert replay.hold_ids == held.hold_ids
        assert replay.points == 8
        assert engine2.available("acct-1") == available
        settled = sink2.settle(
            _text(output_units=5_000),
            account_id="acct-1",
            request_id="req-restart",
            billing="platform",
        )
        assert settled.status == "settled"
        assert settled.points == 4
        assert engine2.balances_match_ledger("acct-1")
    finally:
        store2.close()


def test_transaction_rollback_does_not_ack_partial_money(pg_store: PostgresPointsStore):
    engine = PointsEngine(store=pg_store, clock=lambda: datetime(2026, 11, 9, tzinfo=UTC))
    engine.apply_grant(owner_id="acct-1", amount=100, key="g1")
    before = engine.available("acct-1")
    with pytest.raises(RuntimeError, match="boom"):
        with engine.transaction():
            engine.apply_hold(owner_id="acct-1", amount=40, key="h-boom")
            raise RuntimeError("boom")
    assert engine.available("acct-1") == before
    assert engine.held_amount("acct-1") == 0
    assert pg_store.get_idempotency("h-boom") is None


def test_concurrent_duplicate_hold_does_not_double_debit(pg_store: PostgresPointsStore):
    engine = PointsEngine(store=pg_store, clock=lambda: datetime(2026, 11, 9, tzinfo=UTC))
    engine.apply_grant(owner_id="acct-1", amount=1000, key="g1")
    dsn = pg_store._conninfo
    schema = pg_store.schema

    def once(_: int) -> str:
        store = PostgresPointsStore(dsn, schema=schema)
        try:
            local = PointsEngine(store=store, clock=lambda: datetime(2026, 11, 9, tzinfo=UTC))
            sink = PointsUsageSink(local, RateTable.from_snapshot())
            result = sink.hold(
                _text(),
                account_id="acct-1",
                request_id="req-concurrent",
                billing="platform",
            )
            return f"{result.status}:{result.points}:{result.hold_ids}"
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(once, i) for i in range(8)]
        results = [future.result() for future in as_completed(futures)]

    assert len(set(results)) == 1
    assert engine.available("acct-1") == 992
    assert engine.held_amount("acct-1") == 8


def test_replay_conflict_and_shortfall_terminal(pg_store: PostgresPointsStore):
    engine = PointsEngine(store=pg_store, clock=lambda: datetime(2026, 11, 9, tzinfo=UTC))
    engine.apply_grant(owner_id="acct-1", amount=5, key="g1")
    sink = PointsUsageSink(engine, RateTable.from_snapshot())
    held = sink.hold(
        _text(output_units=5_000),
        account_id="acct-1",
        request_id="req-short",
        billing="platform",
    )
    assert held.status == "held"
    assert held.points == 4
    with pytest.raises(IdempotencyConflict):
        sink.hold(
            _text(output_units=10_000),
            account_id="acct-1",
            request_id="req-short",
            billing="platform",
        )
    short = sink.settle(
        _text(output_units=10_000),
        account_id="acct-1",
        request_id="req-short",
        billing="platform",
    )
    assert short.status == "shortfall"
    assert short.shortfall_points == 4
    again = sink.settle(
        _text(output_units=10_000),
        account_id="acct-1",
        request_id="req-short",
        billing="platform",
    )
    assert again.status == "shortfall"
    assert again.effect is not None
    assert again.effect["outcome"] == "shortfall"


def test_cancel_tombstone_and_no_independent_expiry_scheduler(pg_store: PostgresPointsStore):
    engine = PointsEngine(store=pg_store, clock=lambda: datetime(2026, 11, 9, tzinfo=UTC))
    engine.apply_grant(owner_id="acct-1", amount=1000, key="g1")
    sink = PointsUsageSink(engine, RateTable.from_snapshot())
    cancelled = sink.release(
        account_id="acct-1",
        request_id="req-timeout",
        reason="hold_timeout",
    )
    assert cancelled.status == "cancelled"
    late = sink.hold(
        _text(),
        account_id="acct-1",
        request_id="req-timeout",
        billing="platform",
    )
    assert late.status == "cancelled"
    assert engine.available("acct-1") == 1000
    # Accounts must not create a second hold-expiry scheduler.
    assert not hasattr(engine, "schedule_hold_timeout")
    assert not hasattr(sink, "schedule_hold_timeout")


def test_byok_skip_persists_and_runtime_cannot_ddl(pg_store: PostgresPointsStore):
    engine = PointsEngine(store=pg_store, clock=lambda: datetime(2026, 11, 9, tzinfo=UTC))
    engine.apply_grant(owner_id="acct-1", amount=100, key="g1")
    sink = PointsUsageSink(engine, RateTable.from_snapshot())
    skipped = sink.hold(
        _text(),
        account_id="acct-1",
        request_id="req-byok",
        billing="byok",
    )
    assert skipped.status == "skipped"
    assert skipped.skipped_reason == "byok"
    assert engine.available("acct-1") == 100
    import psycopg.errors

    with pg_store._conn.cursor() as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("CREATE TABLE points.should_not_exist (id INT)")
    pg_store._conn.rollback()
