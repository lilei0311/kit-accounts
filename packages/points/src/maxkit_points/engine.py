"""Lot + entry + hold engine.

There is one available balance per owner. Lot `source` (grant, purchase, promo, adjust)
is provenance for an audit, not a paid bucket and a free bucket.

Allocation order is soonest `expires_at` first, then `created_at`, then id.
Lots with no expiry are spent last. An open hold keeps its points through expiry:
settle still consumes them, release does not put them back into available.

Product SKU prices stay in the application. Pass the price as `amount` and the sku
as `ref` if you need it on the audit line.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from maxkit_contracts import validate
from maxkit_points.errors import (
    HoldStateError,
    IdempotencyConflict,
    InsufficientPoints,
    InvalidPointsArgument,
    NotFound,
    PointsError,
)
from maxkit_points.models import (
    Allocation,
    Balance,
    Entry,
    EntryKind,
    Hold,
    HoldStatus,
    IdempotencyRecord,
    LotSource,
    OwnerKind,
    PointLot,
    isoformat,
)
from maxkit_points.store import MemoryPointsStore, PointsStore

_OWNER_KINDS = ("person", "org")
_SOURCES = ("grant", "purchase", "promo", "adjust")
_NO_EXPIRY = datetime(9999, 12, 31, tzinfo=UTC)


class PointsEngine:
    def __init__(
        self,
        store: PointsStore | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store or MemoryPointsStore()
        self._clock = clock or (lambda: datetime.now(UTC))

    def grant(
        self,
        *,
        owner_id: str,
        amount: int,
        idempotency_key: str,
        owner_kind: OwnerKind = "person",
        source: LotSource = "grant",
        lot_id: str | None = None,
        expires_at: datetime | None = None,
        ref: str | None = None,
    ) -> PointLot:
        owner = _require_text(owner_id, "owner_id")
        key = _require_text(idempotency_key, "idempotency_key")
        _require_amount(amount)
        _require_owner_kind(owner_kind)
        _require_source(source)
        external_ref = _optional_text(ref, "ref")
        expires = _require_aware(expires_at, "expires_at")
        supplied_lot_id = _optional_text(lot_id, "lot_id") if lot_id is not None else None
        payload = _payload_hash(
            {
                "owner_id": owner,
                "owner_kind": owner_kind,
                "amount": amount,
                "source": source,
                "ref": external_ref,
                "expires_at": isoformat(expires),
                "lot_id": supplied_lot_id,
            }
        )
        with self._store.transaction():
            replay = self._replay("grant", key, payload)
            if replay is not None:
                return self._must_lot(replay)
            chosen_id = supplied_lot_id or _new_id("lot")
            self._assert_owner_kind(owner, owner_kind)
            if self._store.get_lot(chosen_id) is not None:
                raise InvalidPointsArgument(f"lot id already exists: {chosen_id}")
            now = self._now()
            lot = PointLot(
                id=chosen_id,
                owner_id=owner,
                owner_kind=owner_kind,
                source=source,
                original_amount=amount,
                remaining=amount,
                reserved=0,
                expires_at=expires,
                created_at=now,
                ref=external_ref,
                idempotency_key=key,
            )
            self._check_lot_invariant(lot)
            validate("point_lot", lot.to_dict())
            entry = self._build_entry(
                owner_id=owner,
                lot_id=lot.id,
                hold_id=None,
                kind="grant",
                amount=amount,
                ref=external_ref,
                idempotency_key=key,
                now=now,
            )
            validate("entry", entry.to_dict())
            self._store.save_lot(lot)
            self._store.append_entry(entry)
            self._store.put_idempotency(
                IdempotencyRecord(key=key, scope="grant", payload_hash=payload, target_id=lot.id)
            )
            return lot

    def apply_hold(
        self,
        *,
        owner_id: str,
        amount: int,
        idempotency_key: str,
        owner_kind: OwnerKind = "person",
        hold_id: str | None = None,
        ref: str | None = None,
    ) -> Hold:
        owner = _require_text(owner_id, "owner_id")
        key = _require_text(idempotency_key, "idempotency_key")
        _require_amount(amount)
        _require_owner_kind(owner_kind)
        external_ref = _optional_text(ref, "ref")
        supplied_hold_id = _optional_text(hold_id, "hold_id") if hold_id is not None else None
        payload = _payload_hash(
            {
                "owner_id": owner,
                "owner_kind": owner_kind,
                "amount": amount,
                "ref": external_ref,
                "hold_id": supplied_hold_id,
            }
        )
        with self._store.transaction():
            replay = self._replay("hold", key, payload)
            if replay is not None:
                return self._must_hold(replay)
            chosen_id = supplied_hold_id or _new_id("hold")
            now = self._now()
            self._assert_owner_kind(owner, owner_kind)
            if self._store.get_hold(chosen_id) is not None:
                raise InvalidPointsArgument(f"hold id already exists: {chosen_id}")
            allocations = self._plan_allocations(owner, amount, now)
            hold = Hold(
                id=chosen_id,
                owner_id=owner,
                owner_kind=owner_kind,
                amount=amount,
                status="open",
                allocations=allocations,
                ref=external_ref,
                idempotency_key=key,
                created_at=now,
                updated_at=now,
            )
            validate("hold", hold.to_dict())
            staged_lots: list[PointLot] = []
            entries: list[Entry] = []
            for allocation in allocations:
                lot = self._must_lot(allocation.lot_id)
                lot.reserved += allocation.amount
                self._check_lot_invariant(lot)
                validate("point_lot", lot.to_dict())
                entry = self._build_entry(
                    owner_id=owner,
                    lot_id=lot.id,
                    hold_id=hold.id,
                    kind="hold",
                    amount=allocation.amount,
                    ref=external_ref,
                    idempotency_key=key,
                    now=now,
                )
                validate("entry", entry.to_dict())
                staged_lots.append(lot)
                entries.append(entry)
            for lot in staged_lots:
                self._store.save_lot(lot)
            for entry in entries:
                self._store.append_entry(entry)
            self._store.save_hold(hold)
            self._store.put_idempotency(
                IdempotencyRecord(key=key, scope="hold", payload_hash=payload, target_id=hold.id)
            )
            return hold

    def settle(self, hold_id: str) -> Hold:
        return self._close_hold(_require_text(hold_id, "hold_id"), "settled")

    def release(self, hold_id: str) -> Hold:
        return self._close_hold(_require_text(hold_id, "hold_id"), "released")

    def expire_due(self, *, owner_id: str | None = None) -> list[Entry]:
        owner = _optional_text(owner_id, "owner_id") if owner_id is not None else None
        with self._store.transaction():
            now = self._now()
            lots = self._store.all_lots() if owner is None else self._store.lots_for_owner(owner)
            lots.sort(key=lambda lot: (_expiry_key(lot), lot.created_at, lot.id))
            written: list[Entry] = []
            for lot in lots:
                if not _is_expired(lot, now):
                    continue
                burn = lot.remaining - lot.reserved
                if burn <= 0:
                    continue
                lot.remaining = lot.reserved
                self._check_lot_invariant(lot)
                entry = self._build_entry(
                    owner_id=lot.owner_id,
                    lot_id=lot.id,
                    hold_id=None,
                    kind="expire",
                    amount=burn,
                    ref=lot.ref,
                    idempotency_key=f"expire:{lot.id}:{isoformat(now)}",
                    now=now,
                )
                validate("point_lot", lot.to_dict())
                validate("entry", entry.to_dict())
                self._store.save_lot(lot)
                self._store.append_entry(entry)
                written.append(entry)
            return written

    def balance(self, owner_id: str) -> Balance:
        owner = _require_text(owner_id, "owner_id")
        with self._store.transaction():
            return self._balance_locked(owner)

    def entries(self, owner_id: str) -> list[Entry]:
        owner = _require_text(owner_id, "owner_id")
        with self._store.transaction():
            return list(self._store.entries_for_owner(owner))

    def get_lot(self, lot_id: str) -> PointLot:
        with self._store.transaction():
            return self._must_lot(_require_text(lot_id, "lot_id"))

    def get_hold(self, hold_id: str) -> Hold:
        with self._store.transaction():
            return self._must_hold(_require_text(hold_id, "hold_id"))

    def _close_hold(self, hold_id: str, target: HoldStatus) -> Hold:
        action = "settle" if target == "settled" else "release"
        with self._store.transaction():
            hold = self._must_hold(hold_id)
            if hold.status == target:
                return hold
            if hold.status != "open":
                raise HoldStateError(hold.id, hold.status, action)
            now = self._now()
            for allocation in hold.allocations:
                lot = self._must_lot(allocation.lot_id)
                if lot.reserved < allocation.amount or lot.remaining < allocation.amount:
                    raise PointsError(f"lot reservation corrupt: {lot.id}")
                lot.reserved -= allocation.amount
                if target == "settled":
                    lot.remaining -= allocation.amount
                    entry_kind = "settle"
                    entry_key = f"settle:{hold.id}"
                else:
                    entry_kind = "release"
                    entry_key = f"release:{hold.id}"
                self._check_lot_invariant(lot)
                entry = self._build_entry(
                    owner_id=hold.owner_id,
                    lot_id=lot.id,
                    hold_id=hold.id,
                    kind=entry_kind,
                    amount=allocation.amount,
                    ref=hold.ref,
                    idempotency_key=entry_key,
                    now=now,
                )
                validate("entry", entry.to_dict())
                self._store.append_entry(entry)
                if target == "released" and _is_expired(lot, now):
                    lot.remaining -= allocation.amount
                    self._check_lot_invariant(lot)
                    expired = self._build_entry(
                        owner_id=hold.owner_id,
                        lot_id=lot.id,
                        hold_id=hold.id,
                        kind="expire",
                        amount=allocation.amount,
                        ref=hold.ref,
                        idempotency_key=f"expire:{hold.id}:{lot.id}",
                        now=now,
                    )
                    validate("entry", expired.to_dict())
                    self._store.append_entry(expired)
                validate("point_lot", lot.to_dict())
                self._store.save_lot(lot)
            hold.status = target
            hold.updated_at = now
            validate("hold", hold.to_dict())
            self._store.save_hold(hold)
            return hold

    def _plan_allocations(self, owner_id: str, amount: int, now: datetime) -> list[Allocation]:
        lots = [lot for lot in self._store.lots_for_owner(owner_id) if _spendable(lot, now) > 0]
        lots.sort(key=lambda lot: (_expiry_key(lot), lot.created_at, lot.id))
        available = sum(_spendable(lot, now) for lot in lots)
        if available < amount:
            raise InsufficientPoints(owner_id, amount, available)
        needed = amount
        allocations: list[Allocation] = []
        for lot in lots:
            take = min(_spendable(lot, now), needed)
            if take <= 0:
                continue
            allocations.append(Allocation(lot_id=lot.id, amount=take))
            needed -= take
            if needed == 0:
                break
        if needed != 0 or not allocations:
            raise PointsError("allocation planning failed")
        return allocations

    def _balance_locked(self, owner_id: str) -> Balance:
        now = self._now()
        lots = self._store.lots_for_owner(owner_id)
        holds = self._store.holds_for_owner(owner_id)
        reserved = sum(lot.reserved for lot in lots)
        held = sum(hold.amount for hold in holds if hold.status == "open")
        if reserved != held:
            raise PointsError(f"reserved does not match open holds for {owner_id}")
        available = sum(_spendable(lot, now) for lot in lots)
        remaining = sum(lot.remaining for lot in lots)
        return Balance(
            owner_id=owner_id,
            available=available,
            held=held,
            remaining=remaining,
        )

    def _replay(self, scope: str, key: str, payload: str) -> str | None:
        existing = self._store.get_idempotency(key)
        if existing is None:
            return None
        if existing.scope != scope or existing.payload_hash != payload:
            raise IdempotencyConflict(key)
        return existing.target_id

    def _assert_owner_kind(self, owner_id: str, owner_kind: OwnerKind) -> None:
        for lot in self._store.lots_for_owner(owner_id):
            if lot.owner_kind != owner_kind:
                raise InvalidPointsArgument(
                    f"owner {owner_id} is already {lot.owner_kind}, not {owner_kind}"
                )
            return

    def _build_entry(
        self,
        *,
        owner_id: str,
        lot_id: str | None,
        hold_id: str | None,
        kind: EntryKind,
        amount: int,
        ref: str | None,
        idempotency_key: str,
        now: datetime,
    ) -> Entry:
        return Entry(
            id=_new_id("ent"),
            owner_id=owner_id,
            lot_id=lot_id,
            hold_id=hold_id,
            kind=kind,
            amount=amount,
            created_at=now,
            ref=ref,
            idempotency_key=idempotency_key,
        )

    def _must_lot(self, lot_id: str) -> PointLot:
        lot = self._store.get_lot(lot_id)
        if lot is None:
            raise NotFound("lot", lot_id)
        return lot

    def _must_hold(self, hold_id: str) -> Hold:
        hold = self._store.get_hold(hold_id)
        if hold is None:
            raise NotFound("hold", hold_id)
        return hold

    def _check_lot_invariant(self, lot: PointLot) -> None:
        if not (0 <= lot.reserved <= lot.remaining <= lot.original_amount):
            raise PointsError(f"lot invariant failed: {lot.id}")

    def _now(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise InvalidPointsArgument("clock must return a timezone-aware datetime")
        return current


def _spendable(lot: PointLot, now: datetime) -> int:
    active = lot.reserved if _is_expired(lot, now) else lot.remaining
    return active - lot.reserved


def _is_expired(lot: PointLot, now: datetime) -> bool:
    return lot.expires_at is not None and lot.expires_at <= now


def _expiry_key(lot: PointLot) -> datetime:
    return lot.expires_at if lot.expires_at is not None else _NO_EXPIRY


def _require_amount(amount: object) -> None:
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise InvalidPointsArgument("amount must be a positive integer")


def _require_owner_kind(owner_kind: str) -> None:
    if owner_kind not in _OWNER_KINDS:
        raise InvalidPointsArgument("owner_kind must be person or org")


def _require_source(source: str) -> None:
    if source not in _SOURCES:
        raise InvalidPointsArgument("source must be grant, purchase, promo, or adjust")


def _require_text(value: str, field: str) -> str:
    text = value.strip()
    if not text:
        raise InvalidPointsArgument(f"{field} must be non-empty")
    return text


def _optional_text(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        raise InvalidPointsArgument(f"{field} must be non-empty when set")
    return text


def _require_aware(value: datetime | None, field: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidPointsArgument(f"{field} must be timezone-aware")
    return value


def _payload_hash(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"
