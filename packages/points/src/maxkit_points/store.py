"""Points persistence port plus the in-memory adapter used by tests and local apps."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock
from typing import Protocol

from maxkit_points.models import Entry, Hold, IdempotencyRecord, PointLot


class PointsStore(Protocol):
    def transaction(self) -> Iterator[None]: ...

    def save_lot(self, lot: PointLot) -> None: ...

    def get_lot(self, lot_id: str) -> PointLot | None: ...

    def lots_for_owner(self, owner_id: str) -> list[PointLot]: ...

    def all_lots(self) -> list[PointLot]: ...

    def save_hold(self, hold: Hold) -> None: ...

    def get_hold(self, hold_id: str) -> Hold | None: ...

    def holds_for_owner(self, owner_id: str) -> list[Hold]: ...

    def append_entry(self, entry: Entry) -> None: ...

    def entries_for_owner(self, owner_id: str) -> list[Entry]: ...

    def get_idempotency(self, key: str) -> IdempotencyRecord | None: ...

    def put_idempotency(self, record: IdempotencyRecord) -> None: ...


class MemoryPointsStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self.lots: dict[str, PointLot] = {}
        self.holds: dict[str, Hold] = {}
        self.entries: list[Entry] = []
        self.idempotency: dict[str, IdempotencyRecord] = {}

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            yield

    def save_lot(self, lot: PointLot) -> None:
        self.lots[lot.id] = lot

    def get_lot(self, lot_id: str) -> PointLot | None:
        return self.lots.get(lot_id)

    def lots_for_owner(self, owner_id: str) -> list[PointLot]:
        rows = [lot for lot in self.lots.values() if lot.owner_id == owner_id]
        return sorted(rows, key=lambda lot: (lot.created_at, lot.id))

    def all_lots(self) -> list[PointLot]:
        return sorted(self.lots.values(), key=lambda lot: (lot.created_at, lot.id))

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

    def get_idempotency(self, key: str) -> IdempotencyRecord | None:
        return self.idempotency.get(key)

    def put_idempotency(self, record: IdempotencyRecord) -> None:
        self.idempotency[record.key] = record
