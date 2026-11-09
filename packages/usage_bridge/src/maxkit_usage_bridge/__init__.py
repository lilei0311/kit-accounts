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
    MeterEnvelopeError,
    MissingRateCard,
    UsageBridgeError,
)
from maxkit_usage_bridge.inbound import (
    SCHEMA_VERSION,
    MeterCallbackAck,
    MeterEventAdapter,
    ParsedMeterEvent,
    expected_event_id,
    parse_meter_event,
)
from maxkit_usage_bridge.models import (
    BillingResult,
    StaticCard,
    UsageEvent,
    UsageShortfall,
)
from maxkit_usage_bridge.rate_snapshot import (
    freeze_rate_snapshot,
    points_from_snapshot,
    verify_snapshot_ref,
)
from maxkit_usage_bridge.rates import RateTable, compute_points, compute_token_points
from maxkit_usage_bridge.sink import PointsUsageSink
from maxkit_usage_bridge.snapshot import STATIC_CARD_SNAPSHOT

__all__ = [
    "BILLABLE_UNIT_KINDS",
    "POINTS_PER_YUAN",
    "SCHEMA_VERSION",
    "STATIC_CARD_SNAPSHOT",
    "BillingResult",
    "BillingStateError",
    "IdempotencyConflict",
    "InsufficientPoints",
    "InvalidUsageArgument",
    "MeterCallbackAck",
    "MeterEnvelopeError",
    "MeterEventAdapter",
    "MissingRateCard",
    "ParsedMeterEvent",
    "PointsUsageSink",
    "RateTable",
    "StaticCard",
    "UsageBridgeError",
    "UsageEvent",
    "UsageShortfall",
    "compute_points",
    "compute_token_points",
    "expected_event_id",
    "freeze_rate_snapshot",
    "parse_meter_event",
    "points_from_snapshot",
    "verify_snapshot_ref",
]

__version__ = "0.1.0"
