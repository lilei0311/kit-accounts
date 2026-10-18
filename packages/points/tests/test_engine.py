import inspect
from datetime import UTC, datetime, timedelta

import pytest

from maxkit_points import (
    PAID_TIER_ORIGINS,
    IdempotencyConflict,
    InsufficientPoints,
    InvalidPointsArgument,
    NotFound,
    PointsEngine,
    rebuild_lots,
)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def test_import_surface_is_consume_track():
    import maxkit_points

    assert maxkit_points.PointsEngine is PointsEngine
    assert "winner_id" not in inspect.signature(PointsEngine.apply_consume_settle).parameters
    assert not hasattr(PointsEngine, "apply_settle")
    assert not hasattr(PointsEngine, "settle")
    assert PAID_TIER_ORIGINS == frozenset({"subscription_grant", "admin_adjustment", "earned"})


def test_replay_without_explicit_ids_does_not_double_grant():
    engine = PointsEngine(clock=lambda: datetime(2026, 10, 16, tzinfo=UTC))
    first = engine.apply_grant(owner_id="p1", amount=1000, key="g1")
    second = engine.apply_grant(owner_id="p1", amount=1000, key="g1")
    assert second.id == first.id
    assert len(engine.entries("p1")) == 1

    held = engine.apply_hold(owner_id="p1", amount=100, key="h1")
    again = engine.apply_hold(owner_id="p1", amount=100, key="h1")
    assert again.id == held.id
    assert again.status == "held"
    assert [entry.kind for entry in engine.entries("p1")] == ["signup_grant", "task_hold"]
    assert engine.entries("p1")[1].delta == -100
    assert engine.get_lot(first.id).opened == 900
    assert engine.get_lot(first.id).amount == 1000


def test_rejects_bool_amount_naive_clock_bad_origin_and_owner_kind_switch():
    engine = PointsEngine(clock=lambda: datetime(2026, 10, 16, tzinfo=UTC))
    with pytest.raises(InvalidPointsArgument):
        engine.apply_grant(owner_id="p1", amount=True, key="g1")  # type: ignore[arg-type]
    with pytest.raises(InvalidPointsArgument):
        engine.apply_grant(
            owner_id="p1",
            amount=10,
            key="g1",
            expires_at=datetime(2026, 10, 17),
        )
    with pytest.raises(InvalidPointsArgument):
        engine.apply_grant(
            owner_id="p1",
            amount=10,
            key="g1",
            origin="purchase",  # type: ignore[arg-type]
        )
    engine.apply_grant(owner_id="p1", amount=10, key="g1", owner_kind="person")
    with pytest.raises(InvalidPointsArgument):
        engine.apply_grant(owner_id="p1", amount=10, key="g2", owner_kind="org")
    naive = PointsEngine(clock=lambda: datetime(2026, 10, 16))
    with pytest.raises(InvalidPointsArgument):
        naive.apply_grant(owner_id="p1", amount=10, key="g1")


def test_missing_hold_and_conflicting_key():
    engine = PointsEngine(clock=lambda: datetime(2026, 10, 16, tzinfo=UTC))
    engine.apply_grant(owner_id="p1", amount=10, key="g1")
    with pytest.raises(NotFound):
        engine.apply_consume_settle("missing")
    with pytest.raises(InsufficientPoints):
        engine.apply_hold(owner_id="p1", amount=11, key="h1")
    engine.apply_hold(owner_id="p1", amount=4, key="h1", hold_id="hold1")
    with pytest.raises(IdempotencyConflict):
        engine.apply_hold(owner_id="p1", amount=4, key="h1", hold_id="other")


def test_balance_is_a_single_number_and_paid_only_is_a_filter():
    clock = Clock(datetime(2026, 10, 16, tzinfo=UTC))
    engine = PointsEngine(clock=clock)
    engine.apply_grant(owner_id="p1", amount=100, key="gift", origin="signup_grant", lot_id="gift")
    clock.now += timedelta(seconds=1)
    engine.apply_grant(owner_id="p1", amount=5, key="earn", origin="earned", lot_id="earn")
    clock.now += timedelta(seconds=1)
    engine.apply_grant(
        owner_id="p1", amount=7, key="admin", origin="admin_adjustment", lot_id="admin"
    )
    clock.now += timedelta(seconds=1)
    engine.apply_grant(
        owner_id="p1", amount=50, key="sub", origin="subscription_grant", lot_id="sub"
    )
    clock.now += timedelta(seconds=1)
    engine.apply_grant(owner_id="p1", amount=3, key="day", origin="daily_claim", lot_id="day")

    assert engine.available("p1") == 165
    assert engine.available("p1", paid_only=True) == 62
    balance = engine.balance("p1")
    assert balance.available == 165
    assert not hasattr(balance, "paid_available")
    assert not hasattr(balance, "free_available")

    held = engine.apply_hold(owner_id="p1", amount=62, key="h", hold_id="h", paid_only=True)
    assert [(item.lot_id, item.amount) for item in held.slices] == [
        ("earn", 5),
        ("admin", 7),
        ("sub", 50),
    ]
    assert engine.get_lot("gift").opened == 100
    assert engine.get_lot("day").opened == 3
    assert engine.balances_match_ledger("p1")


def test_failed_hold_sweeps_expiry_once():
    clock = Clock(datetime(2026, 10, 16, tzinfo=UTC))
    engine = PointsEngine(clock=clock)
    engine.apply_grant(
        owner_id="p1",
        amount=10,
        key="g1",
        expires_at=datetime(2026, 10, 16, 1, tzinfo=UTC),
    )
    clock.now = datetime(2026, 10, 16, 2, tzinfo=UTC)
    with pytest.raises(InsufficientPoints):
        engine.apply_hold(owner_id="p1", amount=1, key="h1")
    with pytest.raises(InsufficientPoints):
        engine.apply_hold(owner_id="p1", amount=1, key="h2")
    expiries = [entry for entry in engine.entries("p1") if entry.kind == "expiry"]
    assert len(expiries) == 1
    assert expiries[0].delta == -10
    assert engine.balance("p1").opened == 0


def test_consume_settle_does_not_move_opened_or_rebuild_a_payee_lot():
    engine = PointsEngine(clock=lambda: datetime(2026, 10, 16, tzinfo=UTC))
    engine.apply_grant(owner_id="p1", amount=80, key="g1", lot_id="lot1")
    engine.apply_hold(owner_id="p1", amount=80, key="h1", hold_id="hold1")
    before = engine.available("p1")
    engine.apply_consume_settle("hold1")
    assert before == 0
    assert engine.available("p1") == 0
    assert engine.held_amount("p1") == 0
    assert engine.get_lot("lot1").opened == 0
    assert engine.get_lot("lot1").amount == 80
    settlements = [entry for entry in engine.entries("p1") if entry.kind == "task_settlement"]
    assert [entry.delta for entry in settlements] == [0]
    rebuilt = rebuild_lots(engine.entries("p1"))
    assert len(rebuilt) == 1
    assert rebuilt[0].opened == 0
    assert engine.balances_match_ledger("p1")


def test_engine_does_not_embed_product_skus():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    assert "dh_std_2m" not in text
    assert "monthlyGrant" not in text
    assert "SIGNUP_GRANT" not in text
