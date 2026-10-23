"""Consume-track points ledger: grant, hold, release, consume settle, expire.

Stable verbs for a future FastAPI thin shell (this repo does not start HTTP):
``PointsEngine.apply_hold`` (idempotency ``key``), ``apply_consume_settle``,
and ``apply_release``. There is no ``settle`` alias — yangong contest-track
``applySettle`` is a different verb.
"""

from maxkit_points.constants import PAID_TIER_ORIGINS
from maxkit_points.engine import PointsEngine
from maxkit_points.errors import (
    HoldStateError,
    IdempotencyConflict,
    InsufficientPoints,
    InvalidPointsArgument,
    NotFound,
    PointsError,
)
from maxkit_points.ledger import rebuild_lots
from maxkit_points.models import ENTRY_KINDS, Balance, Entry, Hold, HoldSlice, PointLot
from maxkit_points.store import MemoryPointsStore, PointsStore

__all__ = [
    "ENTRY_KINDS",
    "PAID_TIER_ORIGINS",
    "Balance",
    "Entry",
    "Hold",
    "HoldSlice",
    "HoldStateError",
    "IdempotencyConflict",
    "InsufficientPoints",
    "InvalidPointsArgument",
    "MemoryPointsStore",
    "NotFound",
    "PointLot",
    "PointsEngine",
    "PointsError",
    "PointsStore",
    "rebuild_lots",
]

__version__ = "0.1.0"
