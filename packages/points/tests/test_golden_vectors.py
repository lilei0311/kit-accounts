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
        assert balance.remaining == step["remaining"]
        assert balance.available + balance.held <= balance.remaining
        return
    if op == "lots":
        for expected in step["lots"]:
            lot = engine.get_lot(expected["id"])
            assert lot.owner_id == step["owner_id"]
            assert lot.remaining == expected["remaining"]
            assert lot.reserved == expected["reserved"]
            assert lot.original_amount == expected["original_amount"]
        return
    if op == "entries":
        actual = engine.entries(step["owner_id"])
        expected = step["entries"]
        assert len(actual) == len(expected)
        for got, want in zip(actual, expected, strict=True):
            assert got.kind == want["kind"]
            assert got.amount == want["amount"]
            if "lot_id" in want:
                assert got.lot_id == want["lot_id"]
            if "hold_id" in want:
                assert got.hold_id == want["hold_id"]
        return
    if op == "expire_due" and step.get("owner_id"):
        owners.add(step["owner_id"])
    _mutate(engine, step)


def _mutate(engine: PointsEngine, step: dict):
    op = step["op"]
    if op == "grant":
        kwargs = {
            "owner_id": step["owner_id"],
            "amount": step["amount"],
            "idempotency_key": step["idempotency_key"],
            "owner_kind": step.get("owner_kind", "person"),
            "source": step.get("source", "grant"),
            "lot_id": step.get("lot_id"),
            "ref": step.get("ref"),
        }
        if "expires_at" in step and step["expires_at"] is not None:
            kwargs["expires_at"] = datetime.fromisoformat(step["expires_at"])
        return engine.grant(**kwargs)
    if op == "apply_hold":
        return engine.apply_hold(
            owner_id=step["owner_id"],
            amount=step["amount"],
            idempotency_key=step["idempotency_key"],
            owner_kind=step.get("owner_kind", "person"),
            hold_id=step.get("hold_id"),
            ref=step.get("ref"),
        )
    if op == "settle":
        return engine.settle(step["hold_id"])
    if op == "release":
        return engine.release(step["hold_id"])
    if op == "expire_due":
        return engine.expire_due(owner_id=step.get("owner_id"))
    raise AssertionError(f"unexpected op {op}")


def _validate_owner(engine: PointsEngine, owner: str) -> None:
    for entry in engine.entries(owner):
        validate("entry", entry.to_dict())
        if entry.lot_id is not None:
            validate("point_lot", engine.get_lot(entry.lot_id).to_dict())
        if entry.hold_id is not None:
            validate("hold", engine.get_hold(entry.hold_id).to_dict())
