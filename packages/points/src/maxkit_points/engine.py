"""Consume-track points engine.

One available balance per owner. Lot ``origin`` / ``source`` record where the
grant came from. They are not a paid bucket and a free bucket. ``paid_only``
is an optional filter (story-clip B-tier) over ``PAID_TIER_ORIGINS``.

Hold decrements ``opened`` and writes a negative ``task_hold``. Release adds
``opened`` back for slices that have not expired; an expired slice gets a
zero-delta ``expiry`` audit and is not restored. ``apply_consume_settle``
marks the hold settled and writes a zero-delta ``task_settlement`` per slice.
It does not restore ``opened`` and it does not grant points to a winner.

Yangong ``applySettle`` (contest track: grant ``earned`` to the winner) is
intentionally absent. Subscription calendars and product SKU prices stay in
the application. Pass the point amount in, and put an external id in ``ref``
when the audit line needs it.
"""

from __future__ import annotations

import hashlib
import json
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
from maxkit_points.ledger import fefo, lot_signature, rebuild_lots, sum_available
from maxkit_points.models import (
    POINT_ORIGINS,
    Balance,
    Entry,
    EntryKind,
    Hold,
    HoldSlice,
    IdempotencyRecord,
    OwnerKind,
    PointLot,
    PointOrigin,
    grant_entry_kind,
    isoformat,
)
from maxkit_points.store import MemoryPointsStore, PointsStore

_OWNER_KINDS = ("person", "org")


class PointsEngine:
    def __init__(
        self,
        store: PointsStore | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store or MemoryPointsStore()
        self._clock = clock or (lambda: datetime.now(UTC))

    def apply_grant(
        self,
        *,
        owner_id: str,
        amount: int,
        key: str,
        origin: PointOrigin = "signup_grant",
        owner_kind: OwnerKind = "person",
        lot_id: str | None = None,
        expires_at: datetime | None = None,
        ref: str | None = None,
    ) -> PointLot:
        """Open a lot. ``key`` is the idempotency key stored as ``grant_key``."""

        owner = _require_text(owner_id, "owner_id")
        grant_key = _require_text(key, "key")
        _require_amount(amount)
        _require_owner_kind(owner_kind)
        _require_origin(origin)
        external_ref = _optional_text(ref, "ref")
        expires = _require_aware(expires_at, "expires_at")
        supplied_lot_id = _optional_text(lot_id, "lot_id") if lot_id is not None else None
        payload = _payload_hash(
            {
                "owner_id": owner,
                "owner_kind": owner_kind,
                "amount": amount,
                "origin": origin,
                "ref": external_ref,
                "expires_at": isoformat(expires),
                "lot_id": supplied_lot_id,
            }
        )
        with self._store.transaction():
            replay = self._replay("grant", grant_key, payload)
            if replay is not None:
                return self._must_lot(replay)
            chosen_id = supplied_lot_id or self._store.next_id("lot")
            self._assert_owner_kind(owner, owner_kind)
            if self._store.get_lot(chosen_id) is not None:
                raise InvalidPointsArgument(f"lot id already exists: {chosen_id}")
            now = self._now()
            lot = PointLot(
                id=chosen_id,
                owner_id=owner,
                owner_kind=owner_kind,
                origin=origin,
                source=origin,
                amount=amount,
                opened=amount,
                expires_at=expires,
                granted_at=now,
                grant_key=grant_key,
            )
            self._check_lot(lot)
            validate("point_lot", lot.to_dict())
            entry = self._build_entry(
                owner_id=owner,
                lot_id=lot.id,
                hold_id=None,
                kind=grant_entry_kind(origin),
                delta=amount,
                ref=external_ref,
                key=grant_key,
                lot_expires_at=expires,
                now=now,
            )
            self._commit_entry(entry)
            self._store.save_lot(lot)
            self._store.put_idempotency(
                IdempotencyRecord(
                    key=grant_key,
                    scope="grant",
                    payload_hash=payload,
                    target_id=lot.id,
                )
            )
            return lot

    def apply_hold(
        self,
        *,
        owner_id: str,
        amount: int,
        key: str,
        owner_kind: OwnerKind = "person",
        hold_id: str | None = None,
        ref: str | None = None,
        paid_only: bool = False,
    ) -> Hold:
        """Reserve points FEFO. Decrements ``opened`` now. Whole hold or nothing.

        Expired ``opened`` on this owner is swept first, including when the
        hold then fails for insufficient points. Replay of ``key`` returns the
        existing hold and does not sweep again.
        """

        owner = _require_text(owner_id, "owner_id")
        hold_key = _require_text(key, "key")
        _require_amount(amount)
        _require_owner_kind(owner_kind)
        if not isinstance(paid_only, bool):
            raise InvalidPointsArgument("paid_only must be a bool")
        external_ref = _optional_text(ref, "ref")
        supplied_hold_id = _optional_text(hold_id, "hold_id") if hold_id is not None else None
        payload = _payload_hash(
            {
                "owner_id": owner,
                "owner_kind": owner_kind,
                "amount": amount,
                "ref": external_ref,
                "hold_id": supplied_hold_id,
                "paid_only": paid_only,
            }
        )
        with self._store.transaction():
            replay = self._replay("hold", hold_key, payload)
            if replay is not None:
                return self._must_hold(replay)
            if supplied_hold_id is not None and self._store.get_hold(supplied_hold_id) is not None:
                raise InvalidPointsArgument(f"hold id already exists: {supplied_hold_id}")
            self._assert_owner_kind(owner, owner_kind)
            now = self._now()
            self._sweep(owner, now)
            slices = self._plan(owner, amount, now, paid_only=paid_only)
            chosen_id = supplied_hold_id or self._store.next_id("hold")
            if self._store.get_hold(chosen_id) is not None:
                raise InvalidPointsArgument(f"hold id already exists: {chosen_id}")
            hold = Hold(
                id=chosen_id,
                owner_id=owner,
                owner_kind=owner_kind,
                amount=amount,
                status="held",
                slices=slices,
                ref=external_ref,
                key=hold_key,
                created_at=now,
                updated_at=now,
            )
            validate("hold", hold.to_dict())
            staged: list[tuple[PointLot, Entry]] = []
            for slice_ in slices:
                lot = self._must_lot(slice_.lot_id)
                lot.opened -= slice_.amount
                self._check_lot(lot)
                validate("point_lot", lot.to_dict())
                entry = self._build_entry(
                    owner_id=owner,
                    lot_id=lot.id,
                    hold_id=hold.id,
                    kind="task_hold",
                    delta=-slice_.amount,
                    ref=external_ref,
                    key=f"{hold_key}:{lot.id}",
                    lot_expires_at=slice_.expires_at,
                    now=now,
                )
                staged.append((lot, entry))
            for lot, entry in staged:
                self._store.save_lot(lot)
                self._commit_entry(entry)
            self._store.save_hold(hold)
            self._store.put_idempotency(
                IdempotencyRecord(
                    key=hold_key,
                    scope="hold",
                    payload_hash=payload,
                    target_id=hold.id,
                )
            )
            return hold

    def apply_release(self, hold_id: str) -> Hold:
        """Return ``opened`` for slices still inside their expiry.

        An expired slice is a zero-delta ``expiry`` line. The held points are
        not put back, and the unheld remainder of the lot is left for
        ``apply_expiry``.
        """

        target_id = _require_text(hold_id, "hold_id")
        with self._store.transaction():
            hold = self._must_hold(target_id)
            if hold.status == "released":
                return hold
            if hold.status != "held":
                raise HoldStateError(hold.id, hold.status, "release")
            now = self._now()
            for slice_ in hold.slices:
                entry_key = f"release:{hold.id}:{slice_.lot_id}"
                if self._store.get_entry_by_key(entry_key) is not None:
                    continue
                expired = slice_.expires_at is not None and slice_.expires_at <= now
                if expired:
                    entry = self._build_entry(
                        owner_id=hold.owner_id,
                        lot_id=slice_.lot_id,
                        hold_id=hold.id,
                        kind="expiry",
                        delta=0,
                        ref=hold.ref,
                        key=entry_key,
                        lot_expires_at=slice_.expires_at,
                        now=now,
                    )
                    self._commit_entry(entry)
                    continue
                lot = self._must_lot(slice_.lot_id)
                lot.opened += slice_.amount
                self._check_lot(lot)
                validate("point_lot", lot.to_dict())
                entry = self._build_entry(
                    owner_id=hold.owner_id,
                    lot_id=slice_.lot_id,
                    hold_id=hold.id,
                    kind="task_release",
                    delta=slice_.amount,
                    ref=hold.ref,
                    key=entry_key,
                    lot_expires_at=slice_.expires_at,
                    now=now,
                )
                self._store.save_lot(lot)
                self._commit_entry(entry)
            hold.status = "released"
            hold.updated_at = now
            validate("hold", hold.to_dict())
            self._store.save_hold(hold)
            return hold

    def apply_consume_settle(self, hold_id: str) -> Hold:
        """Close a hold as consumed.

        Writes one zero-delta ``task_settlement`` per slice. ``opened`` stays
        where the hold left it. No winner lot is created.
        """

        target_id = _require_text(hold_id, "hold_id")
        with self._store.transaction():
            hold = self._must_hold(target_id)
            if hold.status == "settled":
                return hold
            if hold.status != "held":
                raise HoldStateError(hold.id, hold.status, "consume_settle")
            now = self._now()
            for slice_ in hold.slices:
                entry_key = f"settle:{hold.id}:{slice_.lot_id}"
                if self._store.get_entry_by_key(entry_key) is not None:
                    continue
                entry = self._build_entry(
                    owner_id=hold.owner_id,
                    lot_id=slice_.lot_id,
                    hold_id=hold.id,
                    kind="task_settlement",
                    delta=0,
                    ref=hold.ref,
                    key=entry_key,
                    lot_expires_at=slice_.expires_at,
                    now=now,
                )
                self._commit_entry(entry)
            hold.status = "settled"
            hold.updated_at = now
            validate("hold", hold.to_dict())
            self._store.save_hold(hold)
            return hold

    def apply_expiry(self, *, owner_id: str | None = None) -> list[Entry]:
        """Burn ``opened`` on lots whose ``expires_at`` is at or before now.

        Held points were already removed from ``opened``, so they are not
        burned here. Replaying the sweep does not write a second line.
        """

        owner = _optional_text(owner_id, "owner_id") if owner_id is not None else None
        with self._store.transaction():
            return self._sweep(owner, self._now())

    def available(self, owner_id: str, *, paid_only: bool = False) -> int:
        owner = _require_text(owner_id, "owner_id")
        if not isinstance(paid_only, bool):
            raise InvalidPointsArgument("paid_only must be a bool")
        with self._store.transaction():
            return sum_available(
                self._store.lots_for_owner(owner),
                self._now(),
                paid_only=paid_only,
            )

    def held_amount(self, owner_id: str) -> int:
        owner = _require_text(owner_id, "owner_id")
        with self._store.transaction():
            return self._held_locked(owner)

    def balance(self, owner_id: str) -> Balance:
        owner = _require_text(owner_id, "owner_id")
        with self._store.transaction():
            lots = self._store.lots_for_owner(owner)
            now = self._now()
            return Balance(
                owner_id=owner,
                available=sum_available(lots, now, paid_only=False),
                held=self._held_locked(owner),
                opened=sum(lot.opened for lot in lots),
            )

    def balances_match_ledger(self, owner_id: str) -> bool:
        """True when live lots match ``rebuild_lots`` and available agrees.

        Held amounts live on the hold rows. They are not a second copy of
        ``opened``.
        """

        owner = _require_text(owner_id, "owner_id")
        with self._store.transaction():
            live = self._store.lots_for_owner(owner)
            kind: OwnerKind = live[0].owner_kind if live else "person"
            rebuilt = rebuild_lots(self._store.entries_for_owner(owner), owner_kind=kind)
            now = self._now()
            live_sig = sorted(lot_signature(lot) for lot in live)
            rebuilt_sig = sorted(lot_signature(lot) for lot in rebuilt)
            if live_sig != rebuilt_sig:
                return False
            return sum_available(live, now, paid_only=False) == sum_available(
                rebuilt, now, paid_only=False
            )

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

    def _plan(
        self,
        owner_id: str,
        amount: int,
        now: datetime,
        *,
        paid_only: bool,
    ) -> list[HoldSlice]:
        lots = fefo(self._store.lots_for_owner(owner_id), now, paid_only=paid_only)
        spendable = sum(lot.opened for lot in lots)
        if spendable < amount:
            raise InsufficientPoints(owner_id, amount, spendable)
        needed = amount
        slices: list[HoldSlice] = []
        for lot in lots:
            take = min(lot.opened, needed)
            if take <= 0:
                continue
            slices.append(HoldSlice(lot_id=lot.id, amount=take, expires_at=lot.expires_at))
            needed -= take
            if needed == 0:
                break
        if needed != 0 or not slices:
            raise PointsError("allocation planning failed")
        return slices

    def _sweep(self, owner_id: str | None, now: datetime) -> list[Entry]:
        lots = self._store.all_lots() if owner_id is None else self._store.lots_for_owner(owner_id)
        lots.sort(
            key=lambda lot: (
                lot.expires_at is None,
                lot.expires_at or now,
                lot.granted_at,
                lot.id,
            )
        )
        written: list[Entry] = []
        for lot in lots:
            if lot.expires_at is None or lot.expires_at > now or lot.opened <= 0:
                continue
            entry_key = f"expiry:{lot.id}:{isoformat(lot.expires_at)}"
            if self._store.get_entry_by_key(entry_key) is not None:
                raise PointsError(f"expiry already recorded while opened remains: {lot.id}")
            delta = -lot.opened
            entry = self._build_entry(
                owner_id=lot.owner_id,
                lot_id=lot.id,
                hold_id=None,
                kind="expiry",
                delta=delta,
                ref=None,
                key=entry_key,
                lot_expires_at=lot.expires_at,
                now=now,
            )
            lot.opened = 0
            self._check_lot(lot)
            validate("point_lot", lot.to_dict())
            self._store.save_lot(lot)
            self._commit_entry(entry)
            written.append(entry)
        return written

    def _held_locked(self, owner_id: str) -> int:
        return sum(
            hold.amount for hold in self._store.holds_for_owner(owner_id) if hold.status == "held"
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
        lot_id: str,
        hold_id: str | None,
        kind: EntryKind,
        delta: int,
        ref: str | None,
        key: str,
        lot_expires_at: datetime | None,
        now: datetime,
    ) -> Entry:
        return Entry(
            id=self._store.next_id("ent"),
            owner_id=owner_id,
            lot_id=lot_id,
            hold_id=hold_id,
            kind=kind,
            delta=delta,
            at=now,
            key=key,
            lot_expires_at=lot_expires_at,
            ref=ref,
        )

    def _commit_entry(self, entry: Entry) -> None:
        if self._store.get_entry_by_key(entry.key) is not None:
            raise PointsError(f"duplicate entry key: {entry.key}")
        validate("entry", entry.to_dict())
        self._store.append_entry(entry)

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

    def _check_lot(self, lot: PointLot) -> None:
        if lot.source != lot.origin:
            raise PointsError(f"lot source must mirror origin: {lot.id}")
        if lot.amount < 1 or not (0 <= lot.opened <= lot.amount):
            raise PointsError(f"lot invariant failed: {lot.id}")

    def _now(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise InvalidPointsArgument("clock must return a timezone-aware datetime")
        return current


def _require_amount(amount: object) -> None:
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise InvalidPointsArgument("amount must be a positive integer")


def _require_owner_kind(owner_kind: str) -> None:
    if owner_kind not in _OWNER_KINDS:
        raise InvalidPointsArgument("owner_kind must be person or org")


def _require_origin(origin: str) -> None:
    if origin not in POINT_ORIGINS:
        raise InvalidPointsArgument(
            "origin must be signup_grant, subscription_grant, daily_claim, earned, "
            "or admin_adjustment"
        )


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
