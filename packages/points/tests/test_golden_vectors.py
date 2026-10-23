from datetime import datetime

import pytest

from maxkit_contracts import load_points_vectors, validate
from maxkit_points import (
    HoldStateError,
    IdempotencyConflict,
    InsufficientPoints,
    InvalidPointsArgument,
    NotFound,
    PointsEngine,
)

_ERRORS = {
    "insufficient": InsufficientPoints,
    "conflict": IdempotencyConflict,
    "hold_state": HoldStateError,
    "invalid": InvalidPointsArgument,
    "not_found": NotFound,
}
_CREATE_KINDS = {"signup_grant", "subscription_grant", "daily_claim", "admin_adjustment"}


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.mark.parametrize("case", load_points_vectors()["cases"], ids=lambda case: case["id"])
def test_golden_case(case: dict):
    clock = Clock(datetime.fromisoformat(case["now"]))
    engine = PointsEngine(clock=clock)
    owners: set[str] = set()
    for step in case["steps"]:
        _run_step(engine, clock, step, owners)
    for owner in owners:
        assert engine.balances_match_ledger(owner)
        _validate_owner(engine, owner)


def _run_step(engine: PointsEngine, clock: Clock, step: dict, owners: set[str]) -> None:
    op = step["op"]
    if op == "set_now":
        clock.now = datetime.fromisoformat(step["now"])
        return
    if "owner_id" in step:
        owners.add(step["owner_id"])
    expect = step.get("expect")
    if expect is not None:
        with pytest.raises(_ERRORS[expect]):
            _mutate(engine, step)
        return
    if op == "balance":
        balance = engine.balance(step["owner_id"])
        assert balance.available == step["available"]
        assert balance.held == step["held"]
        assert balance.opened == step["opened"]
        assert engine.available(step["owner_id"]) == step["available"]
        assert engine.held_amount(step["owner_id"]) == step["held"]
        if "paid_only_available" in step:
            assert engine.available(step["owner_id"], paid_only=True) == step["paid_only_available"]
        return
    if op == "lots":
        for expected in step["lots"]:
            lot = engine.get_lot(expected["id"])
            assert lot.owner_id == step["owner_id"]
            assert lot.amount == expected["amount"]
            assert lot.opened == expected["opened"]
            assert lot.source == lot.origin
            if "origin" in expected:
                assert lot.origin == expected["origin"]
        return
    if op == "entries":
        actual = engine.entries(step["owner_id"])
        expected = step["entries"]
        assert len(actual) == len(expected)
        for got, want in zip(actual, expected, strict=True):
            assert got.kind == want["kind"]
            assert got.delta == want["delta"]
            if "lot_id" in want:
                assert got.lot_id == want["lot_id"]
            if "hold_id" in want:
                assert got.hold_id == want["hold_id"]
        return
    if op == "hold":
        hold = engine.get_hold(step["hold_id"])
        assert hold.status == step["status"]
        if "slices" in step:
            assert [(item.lot_id, item.amount) for item in hold.slices] == [
                (item["lot_id"], item["amount"]) for item in step["slices"]
            ]
        return
    _mutate(engine, step)


def _mutate(engine: PointsEngine, step: dict):
    op = step["op"]
    expires_at = step.get("expires_at")
    if op == "apply_grant":
        kwargs = {
            "owner_id": step["owner_id"],
            "amount": step["amount"],
            "key": step["key"],
            "owner_kind": step.get("owner_kind", "person"),
            "origin": step.get("origin", "signup_grant"),
            "lot_id": step.get("lot_id"),
            "ref": step.get("ref"),
        }
        if expires_at is not None:
            kwargs["expires_at"] = datetime.fromisoformat(expires_at)
        return engine.apply_grant(**kwargs)
    if op == "apply_hold":
        return engine.apply_hold(
            owner_id=step["owner_id"],
            amount=step["amount"],
            key=step["key"],
            owner_kind=step.get("owner_kind", "person"),
            hold_id=step.get("hold_id"),
            ref=step.get("ref"),
            paid_only=step.get("paid_only", False),
        )
    if op == "apply_consume_settle":
        return engine.apply_consume_settle(step["hold_id"])
    if op == "apply_release":
        return engine.apply_release(step["hold_id"])
    if op == "apply_expiry":
        return engine.apply_expiry(owner_id=step.get("owner_id"))
    raise AssertionError(f"unexpected op {op}")


def _validate_owner(engine: PointsEngine, owner: str) -> None:
    for entry in engine.entries(owner):
        validate("entry", entry.to_dict())
        validate("point_lot", engine.get_lot(entry.lot_id).to_dict())
        if entry.hold_id is not None:
            validate("hold", engine.get_hold(entry.hold_id).to_dict())
        if entry.delta == 0:
            assert entry.kind in {"task_settlement", "expiry", "usage_shortfall"}
        if entry.kind == "usage_shortfall":
            assert entry.delta == 0
        if entry.kind == "task_hold":
            assert entry.delta < 0
        if entry.kind == "task_release":
            assert entry.delta > 0
        if entry.kind in _CREATE_KINDS:
            assert entry.delta > 0
        if entry.kind == "expiry" and entry.delta != 0:
            assert entry.delta < 0
        lot = engine.get_lot(entry.lot_id)
        assert 0 <= lot.opened <= lot.amount
        assert lot.source == lot.origin
