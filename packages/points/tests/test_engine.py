from datetime import UTC, datetime
from pathlib import Path

import pytest

from maxkit_points import (
    IdempotencyConflict,
    InsufficientPoints,
    InvalidPointsArgument,
    NotFound,
    PointsEngine,
)


def test_import_surface():
    import maxkit_points

    assert maxkit_points.PointsEngine is PointsEngine


def test_replay_without_explicit_ids_does_not_double_grant():
    engine = PointsEngine(clock=lambda: datetime(2026, 10, 16, tzinfo=UTC))
    first = engine.grant(owner_id="p1", amount=1000, idempotency_key="g1")
    second = engine.grant(owner_id="p1", amount=1000, idempotency_key="g1")
    assert second.id == first.id
    assert len(engine.entries("p1")) == 1

    held = engine.apply_hold(owner_id="p1", amount=100, idempotency_key="h1")
    again = engine.apply_hold(owner_id="p1", amount=100, idempotency_key="h1")
    assert again.id == held.id
    assert [entry.kind for entry in engine.entries("p1")] == ["grant", "hold"]


def test_rejects_bool_amount_naive_clock_and_owner_kind_switch():
    engine = PointsEngine(clock=lambda: datetime(2026, 10, 16, tzinfo=UTC))
    with pytest.raises(InvalidPointsArgument):
        engine.grant(owner_id="p1", amount=True, idempotency_key="g1")  # type: ignore[arg-type]
    with pytest.raises(InvalidPointsArgument):
        engine.grant(
            owner_id="p1",
            amount=10,
            idempotency_key="g1",
            expires_at=datetime(2026, 10, 17),
        )
    engine.grant(owner_id="p1", amount=10, idempotency_key="g1", owner_kind="person")
    with pytest.raises(InvalidPointsArgument):
        engine.grant(owner_id="p1", amount=10, idempotency_key="g2", owner_kind="org")


def test_missing_hold_and_conflicting_key():
    engine = PointsEngine(clock=lambda: datetime(2026, 10, 16, tzinfo=UTC))
    engine.grant(owner_id="p1", amount=10, idempotency_key="g1")
    with pytest.raises(NotFound):
        engine.settle("missing")
    with pytest.raises(InsufficientPoints):
        engine.apply_hold(owner_id="p1", amount=11, idempotency_key="h1")
    engine.apply_hold(owner_id="p1", amount=4, idempotency_key="h1", hold_id="hold1")
    with pytest.raises(IdempotencyConflict):
        engine.apply_hold(owner_id="p1", amount=4, idempotency_key="h1", hold_id="other")


def test_balance_is_a_single_number():
    engine = PointsEngine(clock=lambda: datetime(2026, 10, 16, tzinfo=UTC))
    engine.grant(owner_id="p1", amount=1000, idempotency_key="a", source="purchase", ref="sku:A")
    engine.grant(owner_id="p1", amount=2500, idempotency_key="b", source="grant", ref="sku:B")
    balance = engine.balance("p1")
    assert balance.available == 3500
    assert not hasattr(balance, "paid_available")
    assert not hasattr(balance, "free_available")


def test_engine_does_not_embed_product_skus():
    root = Path(__file__).resolve().parents[1] / "src"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    assert "dh_std_2m" not in text
