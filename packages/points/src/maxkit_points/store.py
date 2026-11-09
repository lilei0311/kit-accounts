"""Points persistence port plus the in-memory adapter used by tests and local apps.

SQLAlchemy stays out of scope. ``PostgresPointsStore`` is the durable adapter.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock
from typing import Protocol

from maxkit_points.billing_request import BillingRequest
from maxkit_points.models import Entry, Hold, IdempotencyRecord, PointLot


class PointsStore(Protocol):
    def transaction(self) -> Iterator[None]: ...

    def lock_idempotency_key(self, key: str) -> None: ...

    def next_id(self, prefix: str) -> str: ...

    def save_lot(self, lot: PointLot) -> None: ...

    def get_lot(self, lot_id: str) -> PointLot | None: ...

    def lots_for_owner(self, owner_id: str) -> list[PointLot]: ...

    def all_lots(self) -> list[PointLot]: ...

    def save_hold(self, hold: Hold) -> None: ...

    def get_hold(self, hold_id: str) -> Hold | None: ...

    def holds_for_owner(self, owner_id: str) -> list[Hold]: ...

    def append_entry(self, entry: Entry) -> None: ...

    def entries_for_owner(self, owner_id: str) -> list[Entry]: ...

    def get_entry_by_key(self, key: str) -> Entry | None: ...

    def get_idempotency(self, key: str) -> IdempotencyRecord | None: ...

    def put_idempotency(self, record: IdempotencyRecord) -> None: ...


class MemoryPointsStore:
    """In-memory PointsStore + billing request map.

    ``transaction()`` is reentrant and rolls back mutations if the block
    raises, so crash/failure paths stay consistent in tests.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._seq = 0
        self._depth = 0
        self._snapshots: list[dict[str, object]] = []
        self.lots: dict[str, PointLot] = {}
        self.holds: dict[str, Hold] = {}
        self.entries: list[Entry] = []
        self.idempotency: dict[str, IdempotencyRecord] = {}
        self.billing_requests: dict[str, BillingRequest] = {}

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            if self._depth == 0:
                self._snapshots.append(self._snapshot())
            self._depth += 1
            try:
                yield
            except Exception:
                if self._depth == 1:
                    self._restore(self._snapshots[-1])
                raise
            finally:
                self._depth -= 1
                if self._depth == 0:
                    self._snapshots.pop()

    def lock_idempotency_key(self, key: str) -> None:
        # Whole-store RLock already serializes concurrent verbs in-process.
        return None

    def next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_{self._seq:08d}"

    def save_lot(self, lot: PointLot) -> None:
        self.lots[lot.id] = lot

    def get_lot(self, lot_id: str) -> PointLot | None:
        return self.lots.get(lot_id)

    def lots_for_owner(self, owner_id: str) -> list[PointLot]:
        rows = [lot for lot in self.lots.values() if lot.owner_id == owner_id]
        return sorted(rows, key=lambda lot: (lot.granted_at, lot.id))

    def all_lots(self) -> list[PointLot]:
        return sorted(self.lots.values(), key=lambda lot: (lot.granted_at, lot.id))

    def save_hold(self, hold: Hold) -> None:
        self.holds[hold.id] = hold

    def get_hold(self, hold_id: str) -> Hold | None:
        return self.holds.get(hold_id)

    def holds_for_owner(self, owner_id: str) -> list[Hold]:
        rows = [hold for hold in self.holds.values() if hold.owner_id == owner_id]
        return sorted(rows, key=lambda hold: (hold.created_at, hold.id))

    def append_entry(self, entry: Entry) -> None:
        self.entries.append(entry)

    def entries_for_owner(self, owner_id: str) -> list[Entry]:
        return [entry for entry in self.entries if entry.owner_id == owner_id]

    def get_entry_by_key(self, key: str) -> Entry | None:
        for entry in self.entries:
            if entry.key == key:
                return entry
        return None

    def get_idempotency(self, key: str) -> IdempotencyRecord | None:
        return self.idempotency.get(key)

    def put_idempotency(self, record: IdempotencyRecord) -> None:
        self.idempotency[record.key] = record

    def get_billing_request(self, request_id: str) -> BillingRequest | None:
        row = self.billing_requests.get(request_id)
        return None if row is None else deepcopy(row)

    def lock_billing_request(self, request_id: str) -> BillingRequest | None:
        return self.get_billing_request(request_id)

    def save_billing_request(self, request: BillingRequest) -> None:
        existing = self.billing_requests.get(request.request_id)
        if existing is not None and existing.account_id != request.account_id:
            raise ValueError(
                f"account binding immutable for {request.request_id}: "
                f"{existing.account_id} != {request.account_id}"
            )
        if request.created_at is None:
            request.created_at = datetime.now(UTC)
        request.updated_at = datetime.now(UTC)
        self.billing_requests[request.request_id] = deepcopy(request)

    def _snapshot(self) -> dict[str, object]:
        return {
            "seq": self._seq,
            "lots": deepcopy(self.lots),
            "holds": deepcopy(self.holds),
            "entries": deepcopy(self.entries),
            "idempotency": deepcopy(self.idempotency),
            "billing_requests": deepcopy(self.billing_requests),
        }

    def _restore(self, snap: dict[str, object]) -> None:
        self._seq = snap["seq"]  # type: ignore[assignment]
        self.lots = snap["lots"]  # type: ignore[assignment]
        self.holds = snap["holds"]  # type: ignore[assignment]
        self.entries = snap["entries"]  # type: ignore[assignment]
        self.idempotency = snap["idempotency"]  # type: ignore[assignment]
        self.billing_requests = snap["billing_requests"]  # type: ignore[assignment]
