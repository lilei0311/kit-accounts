"""Lot, entry, and hold records for the consume-track ledger.

``amount`` is the original grant. ``opened`` is what a new hold can still
spend. A hold decrements ``opened`` immediately. ``source`` mirrors ``origin``;
it is not a second balance.

Entries are an append-only signed ledger. Lots can be rebuilt from them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

OwnerKind = Literal["person", "org"]
PointOrigin = Literal[
    "signup_grant",
    "subscription_grant",
    "daily_claim",
    "earned",
    "admin_adjustment",
]
EntryKind = Literal[
    "signup_grant",
    "subscription_grant",
    "daily_claim",
    "task_hold",
    "task_release",
    "task_settlement",
    "usage_shortfall",
    "expiry",
    "admin_adjustment",
]
HoldStatus = Literal["held", "released", "settled"]

POINT_ORIGINS: tuple[PointOrigin, ...] = (
    "signup_grant",
    "subscription_grant",
    "daily_claim",
    "earned",
    "admin_adjustment",
)
ENTRY_KINDS: tuple[EntryKind, ...] = (
    "signup_grant",
    "subscription_grant",
    "daily_claim",
    "task_hold",
    "task_release",
    "task_settlement",
    "usage_shortfall",
    "expiry",
    "admin_adjustment",
)


def isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.isoformat()


def grant_entry_kind(origin: PointOrigin) -> EntryKind:
    """Map a grant origin onto the entry vocabulary.

    ``earned`` is recorded as ``task_settlement`` with a positive delta.
    Consume settle uses the same kind with delta 0 and does not create a lot.
    """

    if origin == "earned":
        return "task_settlement"
    return origin


@dataclass(frozen=True)
class HoldSlice:
    lot_id: str
    amount: int
    expires_at: datetime | None

    def to_dict(self) -> dict[str, object]:
        return {
            "lot_id": self.lot_id,
            "amount": self.amount,
            "expires_at": isoformat(self.expires_at),
        }


@dataclass
class PointLot:
    id: str
    owner_id: str
    owner_kind: OwnerKind
    origin: PointOrigin
    source: PointOrigin
    amount: int
    opened: int
    expires_at: datetime | None
    granted_at: datetime
    grant_key: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "owner_kind": self.owner_kind,
            "origin": self.origin,
            "source": self.source,
            "amount": self.amount,
            "opened": self.opened,
            "expires_at": isoformat(self.expires_at),
            "granted_at": isoformat(self.granted_at),
            "grant_key": self.grant_key,
        }


@dataclass(frozen=True)
class Entry:
    id: str
    owner_id: str
    lot_id: str
    hold_id: str | None
    kind: EntryKind
    delta: int
    at: datetime
    key: str
    lot_expires_at: datetime | None
    ref: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "lot_id": self.lot_id,
            "hold_id": self.hold_id,
            "kind": self.kind,
            "delta": self.delta,
            "at": isoformat(self.at),
            "key": self.key,
            "lot_expires_at": isoformat(self.lot_expires_at),
            "ref": self.ref,
        }


@dataclass
class Hold:
    id: str
    owner_id: str
    owner_kind: OwnerKind
    amount: int
    status: HoldStatus
    slices: list[HoldSlice]
    ref: str | None
    key: str
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "owner_kind": self.owner_kind,
            "amount": self.amount,
            "status": self.status,
            "slices": [item.to_dict() for item in self.slices],
            "ref": self.ref,
            "key": self.key,
            "created_at": isoformat(self.created_at),
            "updated_at": isoformat(self.updated_at),
        }


@dataclass(frozen=True)
class Balance:
    """Single balance for one owner.

    ``available`` sums ``opened`` on lots that have not expired.
    ``held`` sums holds still in status ``held``.
    ``opened`` sums ``lot.opened``, including expired points not yet swept.
    Held points are already absent from ``opened``.
    """

    owner_id: str
    available: int
    held: int
    opened: int


@dataclass(frozen=True)
class IdempotencyRecord:
    key: str
    scope: Literal["grant", "hold"]
    payload_hash: str
    target_id: str
