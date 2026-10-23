from datetime import UTC, datetime

from maxkit_points import MemoryPointsStore, PointsEngine
from maxkit_usage_bridge import PointsUsageSink, RateTable, UsageEvent


def test_text_billing_path_with_memory_store():
    store = MemoryPointsStore()
    engine = PointsEngine(store=store, clock=lambda: datetime(2026, 10, 23, tzinfo=UTC))
    engine.apply_grant(owner_id="acct-text", amount=1000, key="grant-text")
    sink = PointsUsageSink(engine, RateTable.from_snapshot())

    estimate = UsageEvent(
        model_id="demo-text-v0",
        modality="text",
        units=10_000,
        unit_kind="tokens",
    )
    held = sink.hold(
        estimate,
        account_id="acct-text",
        request_id="req-text-1",
        billing="platform",
    )
    assert held.status == "held"
    assert held.points == 8
    assert engine.available("acct-text") == 992
    assert engine.held_amount("acct-text") == 8

    actual = UsageEvent(
        model_id="demo-text-v0",
        modality="text",
        units=5_000,
        unit_kind="tokens",
    )
    settled = sink.settle(
        actual,
        account_id="acct-text",
        request_id="req-text-1",
        billing="platform",
    )
    assert settled.status == "settled"
    assert settled.points == 4
    assert settled.request_id == "req-text-1"
    assert engine.available("acct-text") == 996
    assert engine.held_amount("acct-text") == 0
    assert engine.balances_match_ledger("acct-text")

    kinds = [entry.kind for entry in engine.entries("acct-text")]
    assert "signup_grant" in kinds
    assert "task_hold" in kinds
    assert "task_release" in kinds
    assert "task_settlement" in kinds
    assert engine.get_hold(settled.hold_ids[0]).status == "settled"
