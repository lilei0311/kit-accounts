"""Lot, entry, and hold records.

Lots are the balance source of truth. Entries are an append-only audit log.
`source` records where a lot came from. It is not a second balance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

OwnerKind = Literal["person", "org"]
LotSource = Literal["grant", "purchase", "promo", "adjust"]
EntryKind = Literal["grant", "hold", "settle", "release", "expire", "adjust"]
HoldStatus = Literal["open", "settled", "released"]


def isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.isoformat()


@dataclass(frozen=True)
class Allocation:
    lot_id: str
    amount: int

    def to_dict(self) -> dict[str, object]:
        return {"lot_id": self.lot_id, "amount": self.amount}


@dataclass
class PointLot:
    id: str
    owner_id: str
    owner_kind: OwnerKind
    source: LotSource
    original_amount: int
    remaining: int
    reserved: int
    expires_at: datetime | None
    created_at: datetime
    ref: str | None
    idempotency_key: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "owner_kind": self.owner_kind,
            "source": self.source,
            "original_amount": self.original_amount,
            "remaining": self.remaining,
            "reserved": self.reserved,
            "expires_at": isoformat(self.expires_at),
            "created_at": isoformat(self.created_at),
            "ref": self.ref,
            "idempotency_key": self.idempotency_key,
        }


@dataclass
class Entry:
    id: str
    owner_id: str
    lot_id: str | None
    hold_id: str | None
    kind: EntryKind
    amount: int
    created_at: datetime
    ref: str | None
    idempotency_key: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "lot_id": self.lot_id,
            "hold_id": self.hold_id,
            "kind": self.kind,
            "amount": self.amount,
            "created_at": isoformat(self.created_at),
            "ref": self.ref,
            "idempotency_key": self.idempotency_key,
        }


@dataclass
class Hold:
    id: str
    owner_id: str
    owner_kind: OwnerKind
    amount: int
    status: HoldStatus
    allocations: list[Allocation]
    ref: str | None
    idempotency_key: str
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "owner_kind": self.owner_kind,
            "amount": self.amount,
            "status": self.status,
            "allocations": [item.to_dict() for item in self.allocations],
            "ref": self.ref,
            "idempotency_key": self.idempotency_key,
            "created_at": isoformat(self.created_at),
            "updated_at": isoformat(self.updated_at),
        }


@dataclass(frozen=True)
class Balance:
    """Single balance for one owner.

    `available` is what a new hold can reserve. `held` is open reservations.
    `remaining` is the sum of lot.remaining, including expired points not yet swept
    by `expire_due`. After a sweep, available + held == remaining.
    """

    owner_id: str
    available: int
    held: int
    remaining: int


@dataclass(frozen=True)
class IdempotencyRecord:
    key: str
    scope: Literal["grant", "hold"]
    payload_hash: str
    target_id: str
