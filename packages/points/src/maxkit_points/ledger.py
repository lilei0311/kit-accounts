"""Pure consume-track helpers: FEFO, available, and ledger rebuild.

Contest-track settlement (yangong ``applySettle`` granting ``earned`` to a
winner) is not produced here. A positive ``task_settlement`` entry is only
the recorded shape of ``apply_grant(origin="earned")``. Consume settle writes
``task_settlement`` with delta 0, which rebuild ignores.
"""

from __future__ import annotations

from datetime import datetime

from maxkit_points.constants import PAID_TIER_ORIGINS
from maxkit_points.models import Entry, OwnerKind, PointLot, PointOrigin

_CREATE_ORIGIN: dict[str, PointOrigin] = {
    "signup_grant": "signup_grant",
    "subscription_grant": "subscription_grant",
    "daily_claim": "daily_claim",
    "admin_adjustment": "admin_adjustment",
}


def lot_is_open(lot: PointLot, now: datetime) -> bool:
    """Spendable now: some ``opened`` remains and expiry is still in the future."""

    if lot.opened <= 0:
        return False
    if lot.expires_at is not None and lot.expires_at <= now:
        return False
    return True


def sum_available(lots: list[PointLot], now: datetime, *, paid_only: bool) -> int:
    total = 0
    for lot in lots:
        if paid_only and lot.origin not in PAID_TIER_ORIGINS:
            continue
        if lot_is_open(lot, now):
            total += lot.opened
    return total


def fefo(lots: list[PointLot], now: datetime, *, paid_only: bool) -> list[PointLot]:
    """Soonest ``expires_at`` first. Lots with no expiry sort last.

    Ties break by ``granted_at``, then lot id.
    """

    alive = [
        lot
        for lot in lots
        if lot_is_open(lot, now) and (not paid_only or lot.origin in PAID_TIER_ORIGINS)
    ]
    alive.sort(key=_fefo_key)
    return alive


def rebuild_lots(entries: list[Entry], *, owner_kind: OwnerKind = "person") -> list[PointLot]:
    """Rebuild lot ``amount`` (original) and ``opened`` (spendable) from entries.

    Grant-shaped positive deltas set ``amount`` and ``opened``. ``task_hold``,
    ``task_release``, and negative ``expiry`` move ``opened`` only. Delta 0
    (consume settle, ``usage_shortfall`` audit, or release of an
    already-expired slice) is audit-only. ``usage_shortfall`` never moves
    ``opened`` even if a non-zero delta were present — it is not an overdraft.
    """

    lots: dict[str, PointLot] = {}
    ordered = sorted(entries, key=lambda entry: (entry.at, entry.id))
    for entry in ordered:
        if entry.kind == "usage_shortfall":
            continue
        origin = _create_origin(entry)
        if origin is not None and entry.delta > 0:
            current = lots.get(entry.lot_id)
            if current is None:
                lots[entry.lot_id] = PointLot(
                    id=entry.lot_id,
                    owner_id=entry.owner_id,
                    owner_kind=owner_kind,
                    origin=origin,
                    source=origin,
                    amount=entry.delta,
                    opened=entry.delta,
                    expires_at=entry.lot_expires_at,
                    granted_at=entry.at,
                    grant_key=entry.key,
                )
            else:
                current.amount += entry.delta
                current.opened += entry.delta
            continue
        if entry.delta == 0:
            continue
        lot = lots.get(entry.lot_id)
        if lot is None:
            continue
        lot.opened += entry.delta
    return list(lots.values())


def lot_signature(lot: PointLot) -> tuple[object, ...]:
    return (
        lot.id,
        lot.owner_id,
        lot.owner_kind,
        lot.origin,
        lot.source,
        lot.amount,
        lot.opened,
        lot.expires_at,
        lot.granted_at,
        lot.grant_key,
    )


def _create_origin(entry: Entry) -> PointOrigin | None:
    mapped = _CREATE_ORIGIN.get(entry.kind)
    if mapped is not None:
        return mapped
    if entry.kind == "task_settlement" and entry.delta > 0:
        return "earned"
    return None


def _fefo_key(lot: PointLot) -> tuple[bool, datetime, datetime, str]:
    stamp = lot.expires_at if lot.expires_at is not None else lot.granted_at
    return (lot.expires_at is None, stamp, lot.granted_at, lot.id)
