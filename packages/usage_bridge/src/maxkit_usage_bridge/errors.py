"""Usage-bridge failures. No payment data, no secrets."""


class UsageBridgeError(Exception):
    """Base error for the usage→points bridge."""


class InvalidUsageArgument(UsageBridgeError):
    """Malformed event, account id, request id, or billing flag."""


class MissingRateCard(UsageBridgeError):
    """Platform billing refused because (model_id, unit_kind) has no card."""

    def __init__(self, model_id: str, unit_kind: str) -> None:
        super().__init__(f"no rate card for platform billing: ({model_id}, {unit_kind})")
        self.model_id = model_id
        self.unit_kind = unit_kind


class BillingStateError(UsageBridgeError):
    def __init__(self, request_id: str, status: str, action: str) -> None:
        super().__init__(f"cannot {action} request {request_id} in status {status}")
        self.request_id = request_id
        self.status = status
        self.action = action
