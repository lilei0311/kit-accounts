"""UsageEvent → consume-track points bridge.

Public call surface for a future FastAPI thin shell (this repo does not
start HTTP). Import these names plus ``PointsEngine.apply_hold`` /
``apply_consume_settle`` / ``apply_release`` from ``maxkit_points``.
Never import kit-model-router from this package.
"""

from maxkit_points import IdempotencyConflict, InsufficientPoints
from maxkit_usage_bridge.constants import BILLABLE_UNIT_KINDS, POINTS_PER_YUAN
from maxkit_usage_bridge.errors import (
    BillingStateError,
    InvalidUsageArgument,
    MissingRateCard,
    UsageBridgeError,
)
from maxkit_usage_bridge.models import (
    BillingResult,
    StaticCard,
    UsageEvent,
    UsageShortfall,
)
from maxkit_usage_bridge.rates import RateTable, compute_points, compute_token_points
from maxkit_usage_bridge.sink import PointsUsageSink
from maxkit_usage_bridge.snapshot import STATIC_CARD_SNAPSHOT

__all__ = [
    "BILLABLE_UNIT_KINDS",
    "POINTS_PER_YUAN",
    "STATIC_CARD_SNAPSHOT",
    "BillingResult",
    "BillingStateError",
    "IdempotencyConflict",
    "InsufficientPoints",
    "InvalidUsageArgument",
    "MissingRateCard",
    "PointsUsageSink",
    "RateTable",
    "StaticCard",
    "UsageBridgeError",
    "UsageEvent",
    "UsageShortfall",
    "compute_points",
    "compute_token_points",
]

__version__ = "0.1.0"
