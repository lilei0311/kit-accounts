"""Durable request lifecycle mapping for hold / settle / release.

``request_id`` is the sole external identity (Hub-generated or caller-provided).
Transport ``event_id`` values are never keys. Same request + same financial
intent replays the persisted terminal result; mutated intent conflicts.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from typing import Any, Literal, Protocol

BillingRequestStatus = Literal[
    "pending_config",
    "held",
    "settled",
    "released",
    "cancelled",
    "rejected",
    "shortfall",
    "skipped",
]

TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        "settled",
        "released",
        "cancelled",
        "rejected",
        "shortfall",
        "skipped",
    }
)


@dataclass
class BillingRequest:
    """Local request row bound immutably to ``account_id`` once created."""

    request_id: str
    account_id: str
    status: BillingRequestStatus
    payload_hash: str = ""
    settle_payload_hash: str | None = None
    rate_snapshot: dict[str, Any] | None = None
    snapshot_ref: str | None = None
    hold_ids: list[str] = field(default_factory=list)
    points: int = 0
    estimated: bool = False
    shortfall_points: int = 0
    skipped_reason: str | None = None
    reject_reason: str | None = None
    model_id: str = ""
    unit_kind: str = ""
    available_after: int | None = None
    effect: dict[str, Any] = field(default_factory=dict)
    billing: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "account_id": self.account_id,
            "status": self.status,
            "payload_hash": self.payload_hash,
            "settle_payload_hash": self.settle_payload_hash,
            "rate_snapshot": self.rate_snapshot,
            "snapshot_ref": self.snapshot_ref,
            "hold_ids": list(self.hold_ids),
            "points": self.points,
            "estimated": self.estimated,
            "shortfall_points": self.shortfall_points,
            "skipped_reason": self.skipped_reason,
            "reject_reason": self.reject_reason,
            "model_id": self.model_id,
            "unit_kind": self.unit_kind,
            "available_after": self.available_after,
            "effect": dict(self.effect),
            "billing": self.billing,
        }


class BillingRequestStore(Protocol):
    """Persistence seam for request lifecycle. Joins the points transaction."""

    def get_billing_request(self, request_id: str) -> BillingRequest | None: ...

    def lock_billing_request(self, request_id: str) -> BillingRequest | None: ...

    def save_billing_request(self, request: BillingRequest) -> None: ...


class MemoryBillingRequestStore:
    """Process-local request map. Used by default with MemoryPointsStore."""

    def __init__(self) -> None:
        self._lock = Lock()
        self.requests: dict[str, BillingRequest] = {}

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            yield

    def get_billing_request(self, request_id: str) -> BillingRequest | None:
        row = self.requests.get(request_id)
        return None if row is None else _copy_request(row)

    def lock_billing_request(self, request_id: str) -> BillingRequest | None:
        return self.get_billing_request(request_id)

    def save_billing_request(self, request: BillingRequest) -> None:
        existing = self.requests.get(request.request_id)
        if existing is not None and existing.account_id != request.account_id:
            raise ValueError(
                f"account binding immutable for {request.request_id}: "
                f"{existing.account_id} != {request.account_id}"
            )
        self.requests[request.request_id] = _copy_request(request)


def _copy_request(request: BillingRequest) -> BillingRequest:
    return BillingRequest(
        request_id=request.request_id,
        account_id=request.account_id,
        status=request.status,
        payload_hash=request.payload_hash,
        settle_payload_hash=request.settle_payload_hash,
        rate_snapshot=None if request.rate_snapshot is None else dict(request.rate_snapshot),
        snapshot_ref=request.snapshot_ref,
        hold_ids=list(request.hold_ids),
        points=request.points,
        estimated=request.estimated,
        shortfall_points=request.shortfall_points,
        skipped_reason=request.skipped_reason,
        reject_reason=request.reject_reason,
        model_id=request.model_id,
        unit_kind=request.unit_kind,
        available_after=request.available_after,
        effect=dict(request.effect),
        billing=request.billing,
        created_at=request.created_at,
        updated_at=request.updated_at,
    )
