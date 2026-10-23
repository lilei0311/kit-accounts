"""(model_id, unit_kind) → yuan_per_unit, then ceil to points."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import ROUND_CEILING, Decimal

from maxkit_usage_bridge.constants import BILLABLE_UNIT_KINDS, POINTS_PER_YUAN
from maxkit_usage_bridge.errors import InvalidUsageArgument, MissingRateCard
from maxkit_usage_bridge.models import StaticCard
from maxkit_usage_bridge.snapshot import STATIC_CARD_SNAPSHOT

_MILLION = Decimal("1000000")
_THOUSAND = Decimal("1000")


class RateTable:
    """Internal cost table. Injectable. Not a public price list."""

    def __init__(
        self,
        cards: Sequence[StaticCard] | None = None,
        *,
        rates: Mapping[tuple[str, str], Decimal | float | int] | None = None,
        points_per_yuan: int = POINTS_PER_YUAN,
    ) -> None:
        if isinstance(points_per_yuan, bool) or not isinstance(points_per_yuan, int):
            raise InvalidUsageArgument("points_per_yuan must be a positive integer")
        if points_per_yuan < 1:
            raise InvalidUsageArgument("points_per_yuan must be a positive integer")
        self.points_per_yuan = points_per_yuan
        self._rates: dict[tuple[str, str], Decimal] = {}
        if cards:
            for card in cards:
                self._ingest_card(card)
        if rates:
            for key, value in rates.items():
                if not isinstance(key, tuple) or len(key) != 2:
                    raise InvalidUsageArgument("rate key must be (model_id, unit_kind)")
                model_id, unit_kind = key
                self._rates[_rate_key(model_id, unit_kind)] = _positive_yuan(value)

    @classmethod
    def from_snapshot(cls, *, points_per_yuan: int = POINTS_PER_YUAN) -> RateTable:
        return cls(STATIC_CARD_SNAPSHOT, points_per_yuan=points_per_yuan)

    def yuan_per_unit(self, model_id: str, unit_kind: str) -> Decimal:
        key = _rate_key(model_id, unit_kind)
        try:
            return self._rates[key]
        except KeyError:
            raise MissingRateCard(key[0], key[1]) from None

    def points_for(self, model_id: str, unit_kind: str, units: int | float | Decimal) -> int:
        return compute_points(
            units,
            self.yuan_per_unit(model_id, unit_kind),
            self.points_per_yuan,
        )

    def has_rate(self, model_id: str, unit_kind: str) -> bool:
        model = model_id.strip()
        kind = unit_kind.strip()
        if not model or kind not in BILLABLE_UNIT_KINDS:
            return False
        return (model, kind) in self._rates

    def _ingest_card(self, card: StaticCard) -> None:
        model_id = _require_text(card.model_id, "model_id")
        token_yuan = _token_yuan_per_unit(card)
        if token_yuan is not None:
            self._rates[(model_id, "tokens")] = token_yuan
        if card.price_per_image_yuan is not None:
            self._rates[(model_id, "images")] = _positive_yuan(card.price_per_image_yuan)
        if card.price_per_clip_yuan is not None:
            self._rates[(model_id, "clips")] = _positive_yuan(card.price_per_clip_yuan)
        if card.price_per_1k_chars_yuan is not None:
            per_1k = _positive_yuan(card.price_per_1k_chars_yuan)
            self._rates[(model_id, "chars")] = per_1k / _THOUSAND


def compute_points(
    units: int | float | Decimal,
    yuan_per_unit: Decimal,
    points_per_yuan: int = POINTS_PER_YUAN,
) -> int:
    """ceil(units × yuan_per_unit × POINTS_PER_YUAN); min 1 when units > 0."""

    amount = _as_decimal(units, "units")
    if amount < 0:
        raise InvalidUsageArgument("units must be >= 0")
    if amount == 0:
        return 0
    if yuan_per_unit <= 0:
        raise InvalidUsageArgument("yuan_per_unit must be positive")
    raw = amount * yuan_per_unit * Decimal(points_per_yuan)
    points = int(raw.to_integral_value(rounding=ROUND_CEILING))
    return max(1, points)


def _token_yuan_per_unit(card: StaticCard) -> Decimal | None:
    prices = [
        _as_decimal(value, "token_price")
        for value in (card.price_per_million_input, card.price_per_million_output)
        if value is not None
    ]
    if not prices:
        return None
    # v0: single UsageEvent.units. Conservative — do not split input/output.
    return max(prices) / _MILLION


def _rate_key(model_id: str, unit_kind: str) -> tuple[str, str]:
    model = _require_text(model_id, "model_id")
    kind = _require_text(unit_kind, "unit_kind")
    if kind not in BILLABLE_UNIT_KINDS:
        raise MissingRateCard(model, kind)
    return model, kind


def _positive_yuan(value: int | float | Decimal) -> Decimal:
    amount = _as_decimal(value, "yuan_per_unit")
    if amount <= 0:
        raise InvalidUsageArgument("yuan_per_unit must be positive")
    return amount


def _as_decimal(value: int | float | Decimal, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise InvalidUsageArgument(f"{field} must be a number")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    return Decimal(str(value))


def _require_text(value: str, field: str) -> str:
    text = value.strip()
    if not text:
        raise InvalidUsageArgument(f"{field} must be non-empty")
    return text
