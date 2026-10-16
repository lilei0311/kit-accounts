"""Points failures. Amounts and owner ids only; no payment data."""


class PointsError(Exception):
    """Base error for the points engine."""


class InvalidPointsArgument(PointsError):
    """Malformed amount, id, owner kind, or timestamp."""


class InsufficientPoints(PointsError):
    def __init__(self, owner_id: str, requested: int, available: int) -> None:
        super().__init__(
            f"insufficient points for {owner_id}: requested {requested}, available {available}"
        )
        self.owner_id = owner_id
        self.requested = requested
        self.available = available


class IdempotencyConflict(PointsError):
    def __init__(self, key: str) -> None:
        super().__init__(f"idempotency key reused with a different payload: {key}")
        self.key = key


class HoldStateError(PointsError):
    def __init__(self, hold_id: str, status: str, action: str) -> None:
        super().__init__(f"cannot {action} hold {hold_id} in status {status}")
        self.hold_id = hold_id
        self.status = status
        self.action = action


class NotFound(PointsError):
    def __init__(self, kind: str, record_id: str) -> None:
        super().__init__(f"{kind} not found: {record_id}")
        self.kind = kind
        self.record_id = record_id
