from datetime import UTC, datetime

import pytest

from maxkit_points import (
    ENTRY_KINDS,
    IdempotencyConflict,
    InsufficientPoints,
    MemoryPointsStore,
    PointsEngine,
)
from maxkit_usage_bridge import (
    BillingStateError,
    InvalidUsageArgument,
    MissingRateCard,
    PointsUsageSink,
    RateTable,
    UsageEvent,
    UsageShortfall,
)


def _engine(amount: int = 1000) -> PointsEngine:
    engine = PointsEngine(
        store=MemoryPointsStore(),
        clock=lambda: datetime(2026, 10, 23, tzinfo=UTC),
    )
    engine.apply_grant(owner_id="acct-1", amount=amount, key="grant-1")
    return engine


def _sink(
    engine: PointsEngine | None = None, *, amount: int = 1000
) -> tuple[PointsEngine, PointsUsageSink]:
    engine = engine or _engine(amount)
    return engine, PointsUsageSink(engine, RateTable.from_snapshot())


def _text(
    *,
    input_units: int | None = None,
    output_units: int | None = None,
    units: int | None = None,
) -> UsageEvent:
    return UsageEvent(
        model_id="demo-text-v0",
        modality="text",
        units=units,
        unit_kind="tokens",
        input_units=input_units,
        output_units=output_units,
    )


def test_hold_rejects_insufficient_balance():
    engine, sink = _sink(amount=3)
    with pytest.raises(InsufficientPoints):
        sink.hold(
            _text(input_units=0, output_units=10_000),
            account_id="acct-1",
            request_id="req-1",
            billing="platform",
        )
    assert engine.available("acct-1") == 3
    assert sink.get("req-1") is None


def test_settle_adjusts_down_and_up():
    engine, sink = _sink()
    held = sink.hold(
        _text(input_units=0, output_units=10_000),
        account_id="acct-1",
        request_id="down",
        billing="platform",
    )
    assert held.points == 8
    assert engine.available("acct-1") == 992
    settled = sink.settle(
        _text(input_units=0, output_units=5_000),
        account_id="acct-1",
        request_id="down",
        billing="platform",
    )
    assert settled.status == "settled"
    assert settled.points == 4
    assert engine.available("acct-1") == 996
    kinds = [entry.kind for entry in engine.entries("acct-1")]
    assert kinds.count("task_hold") == 2
    assert "task_release" in kinds
    assert "task_settlement" in kinds

    held_up = sink.hold(
        _text(input_units=0, output_units=5_000),
        account_id="acct-1",
        request_id="up",
        billing="platform",
    )
    assert held_up.points == 4
    settled_up = sink.settle(
        _text(input_units=0, output_units=10_000),
        account_id="acct-1",
        request_id="up",
        billing="platform",
    )
    assert settled_up.status == "settled"
    assert settled_up.points == 8
    assert engine.available("acct-1") == 988
    assert engine.held_amount("acct-1") == 0


def test_hold_and_settle_use_io_split_not_conservative_max():
    engine, sink = _sink()
    held = sink.hold(
        _text(input_units=1_000_000, output_units=0),
        account_id="acct-1",
        request_id="split",
        billing="platform",
    )
    assert held.points == 200
    assert engine.available("acct-1") == 800
    settled = sink.settle(
        _text(input_units=500_000, output_units=500_000),
        account_id="acct-1",
        request_id="split",
        billing="platform",
    )
    assert settled.status == "settled"
    assert settled.points == 500
    assert engine.available("acct-1") == 500


def test_tokens_total_only_units_are_refused():
    _, sink = _sink()
    with pytest.raises(InvalidUsageArgument):
        sink.hold(_text(units=10_000), account_id="acct-1", request_id="tot", billing="platform")
    with pytest.raises(InvalidUsageArgument):
        sink.hold(_text(), account_id="acct-1", request_id="miss-io", billing="platform")


def test_release_on_failure_restores_opened():
    engine, sink = _sink()
    sink.hold(
        _text(input_units=0, output_units=10_000),
        account_id="acct-1",
        request_id="fail",
        billing="platform",
    )
    released = sink.release(account_id="acct-1", request_id="fail")
    assert released.status == "released"
    assert engine.available("acct-1") == 1000
    assert engine.held_amount("acct-1") == 0
    again = sink.release(account_id="acct-1", request_id="fail")
    assert again.status == "released"


def test_byok_is_noop():
    engine, sink = _sink()
    result = sink.hold(
        _text(input_units=0, output_units=10_000),
        account_id="acct-1",
        request_id="byok",
        billing="byok",
    )
    assert result.status == "skipped"
    assert result.skipped_reason == "byok"
    assert result.points == 0
    assert engine.available("acct-1") == 1000
    settled = sink.settle(
        _text(input_units=0, output_units=10_000),
        account_id="acct-1",
        request_id="byok",
        billing="byok",
    )
    assert settled.status == "skipped"


def test_missing_card_refuses_platform_billing():
    _, sink = _sink()
    event = UsageEvent(
        model_id="no-card",
        modality="text",
        units=None,
        unit_kind="tokens",
        input_units=10,
        output_units=0,
    )
    with pytest.raises(MissingRateCard):
        sink.hold(event, account_id="acct-1", request_id="miss", billing="platform")


def test_seconds_and_zero_units_skip():
    engine, sink = _sink()
    seconds = UsageEvent(model_id="demo-video-v0", modality="video", units=3, unit_kind="seconds")
    skipped = sink.hold(seconds, account_id="acct-1", request_id="sec", billing="platform")
    assert skipped.status == "skipped"
    assert skipped.skipped_reason == "unbillable_unit"
    zero = sink.hold(
        _text(input_units=0, output_units=0),
        account_id="acct-1",
        request_id="zero",
        billing="platform",
    )
    assert zero.skipped_reason == "no_usage"
    assert engine.available("acct-1") == 1000


def test_missing_non_token_units_uses_one_and_marks_estimated():
    engine, sink = _sink()
    event = UsageEvent(model_id="demo-image-v0", modality="image", units=None, unit_kind="images")
    held = sink.hold(event, account_id="acct-1", request_id="est", billing="platform")
    assert held.estimated is True
    assert held.points == 4
    settled = sink.settle(event, account_id="acct-1", request_id="est", billing="platform")
    assert settled.status == "settled"
    assert settled.points == 4
    assert engine.available("acct-1") == 996


def test_idempotent_replay_does_not_double_charge():
    engine, sink = _sink()
    first = sink.hold(
        _text(input_units=0, output_units=10_000),
        account_id="acct-1",
        request_id="idem",
        billing="platform",
    )
    second = sink.hold(
        _text(input_units=0, output_units=10_000),
        account_id="acct-1",
        request_id="idem",
        billing="platform",
    )
    assert second.hold_ids == first.hold_ids
    assert engine.available("acct-1") == 992
    settled = sink.settle(
        _text(input_units=0, output_units=10_000),
        account_id="acct-1",
        request_id="idem",
        billing="platform",
    )
    again = sink.settle(
        _text(input_units=0, output_units=10_000),
        account_id="acct-1",
        request_id="idem",
        billing="platform",
    )
    assert again.status == settled.status == "settled"
    assert again.points == 8
    assert engine.available("acct-1") == 992
    with pytest.raises(IdempotencyConflict):
        sink.hold(
            _text(input_units=0, output_units=5_000),
            account_id="acct-1",
            request_id="idem",
            billing="platform",
        )


def test_shortfall_settles_held_and_writes_usage_shortfall_audit():
    alerts: list[UsageShortfall] = []
    engine = _engine(amount=5)
    sink = PointsUsageSink(engine, RateTable.from_snapshot(), on_shortfall=alerts.append)
    sink.hold(
        _text(input_units=0, output_units=5_000),
        account_id="acct-1",
        request_id="short",
        billing="platform",
    )
    result = sink.settle(
        _text(input_units=0, output_units=10_000),
        account_id="acct-1",
        request_id="short",
        billing="platform",
    )
    assert result.status == "shortfall"
    assert result.points == 4
    assert result.shortfall_points == 4
    assert engine.available("acct-1") == 1
    assert alerts[0].shortfall_points == 4
    kinds = [entry.kind for entry in engine.entries("acct-1")]
    assert "usage_shortfall" in ENTRY_KINDS
    assert kinds.count("usage_shortfall") == 1
    short = next(entry for entry in engine.entries("acct-1") if entry.kind == "usage_shortfall")
    assert short.delta == 0
    assert short.ref == "short"
    assert engine.balances_match_ledger("acct-1")
    replay = sink.settle(
        _text(input_units=0, output_units=10_000),
        account_id="acct-1",
        request_id="short",
        billing="platform",
    )
    assert replay.status == "shortfall"
    assert [entry.kind for entry in engine.entries("acct-1")].count("usage_shortfall") == 1


def test_settle_without_hold_is_refused_for_platform():
    _, sink = _sink()
    with pytest.raises(BillingStateError):
        sink.settle(
            _text(input_units=0, output_units=10),
            account_id="acct-1",
            request_id="nohold",
            billing="platform",
        )


def test_cannot_release_after_settle():
    _, sink = _sink()
    sink.hold(
        _text(input_units=0, output_units=10_000),
        account_id="acct-1",
        request_id="done",
        billing="platform",
    )
    sink.settle(
        _text(input_units=0, output_units=10_000),
        account_id="acct-1",
        request_id="done",
        billing="platform",
    )
    with pytest.raises(BillingStateError):
        sink.release(account_id="acct-1", request_id="done")
