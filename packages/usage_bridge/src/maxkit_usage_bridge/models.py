"""Usage event, STATIC_CARDS field snapshot, and sink results.

``UsageEvent`` matches the kit-model-router emit shape: model, modality,
unit_kind, plus tokens ``input_units``/``output_units`` or non-token
``units``. ``account_id`` and ``request_id`` stay in application context,
not on the event.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from maxkit_usage_bridge.constants import POINTS_PER_YUAN

Modality = Literal["text", "image", "video", "tts"]
UnitKind = Literal["tokens", "images", "clips", "chars", "seconds"]
BillingMode = Literal["platform", "byok"]
BillingStatus = Literal["held", "settled", "released", "skipped", "shortfall"]


@dataclass(frozen=True)
class UsageEvent:
    model_id: str
    modality: str
    units: int | float | Decimal | None
    unit_kind: str
    input_units: int | float | Decimal | None = None
    output_units: int | float | Decimal | None = None


@dataclass(frozen=True)
class StaticCard:
    """Frozen STATIC_CARDS field subset. Not a live kit-model-router import.

    Yuan fields are internal cost-equivalent inputs for RateTable, not
    public SKU prices.
    """

    model_id: str
    price_per_million_input: float | int | Decimal | None = None
    price_per_million_output: float | int | Decimal | None = None
    price_per_image_yuan: float | int | Decimal | None = None
    price_per_clip_yuan: float | int | Decimal | None = None
    price_per_1k_chars_yuan: float | int | Decimal | None = None


@dataclass(frozen=True)
class UsageShortfall:
    """Bridge-level audit when actual points exceed the hold and top-up fails.

    Also written as a formal ``usage_shortfall`` ledger entry (delta 0).
    That line is audit-only: it is not an overdraft and does not change
    ``opened``.
    """

    request_id: str
    account_id: str
    model_id: str
    unit_kind: str
    held_points: int
    actual_points: int
    shortfall_points: int


@dataclass(frozen=True)
class BillingResult:
    request_id: str
    account_id: str
    status: BillingStatus
    points: int
    hold_ids: tuple[str, ...]
    estimated: bool = False
    shortfall_points: int = 0
    skipped_reason: str | None = None
    points_per_yuan: int = POINTS_PER_YUAN
