"""RateTable: tokens use in/out keys; other modalities use a single unit rate.

Dual-ledger §0.3: ``points = ceil(input_units × rate_in + output_units × rate_out)``
for tokens. ``rate_*`` is yuan-per-unit × ``POINTS_PER_YUAN``. A conservative
single-leg / ``max(in, out)`` / total-only token rate is abolished.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import ROUND_CEILING, Decimal

from maxkit_usage_bridge.constants import BILLABLE_UNIT_KINDS, POINTS_PER_YUAN
from maxkit_usage_bridge.errors import InvalidUsageArgument, MissingRateCard
from maxkit_usage_bridge.models import StaticCard
from maxkit_usage_bridge.snapshot import STATIC_CARD_SNAPSHOT

_MILLION = Decimal("1000000")
_THOUSAND = Decimal("1000")
_TOKEN_DIRECTIONS = frozenset({"in", "out"})


class RateTable:
    """Internal cost table. Injectable. Not a public price list.

    Non-token keys: ``(model_id, unit_kind) → yuan_per_unit``.
    Token keys: ``(model_id, "tokens", "in"|"out") → yuan_per_token``.
    """

    def __init__(
        self,
        cards: Sequence[StaticCard] | None = None,
        *,
        rates: Mapping[tuple[str, str] | tuple[str, str, str], Decimal | float | int] | None = None,
        points_per_yuan: int = POINTS_PER_YUAN,
    ) -> None:
        if isinstance(points_per_yuan, bool) or not isinstance(points_per_yuan, int):
            raise InvalidUsageArgument("points_per_yuan must be a positive integer")
        if points_per_yuan < 1:
            raise InvalidUsageArgument("points_per_yuan must be a positive integer")
        self.points_per_yuan = points_per_yuan
        self._rates: dict[tuple[str, str], Decimal] = {}
        self._token_rates: dict[tuple[str, str], Decimal] = {}
        if cards:
            for card in cards:
                self._ingest_card(card)
        if rates:
            for key, value in rates.items():
                self._ingest_rate_key(key, value)

    @classmethod
    def from_snapshot(cls, *, points_per_yuan: int = POINTS_PER_YUAN) -> RateTable:
        return cls(STATIC_CARD_SNAPSHOT, points_per_yuan=points_per_yuan)

    def yuan_per_unit(self, model_id: str, unit_kind: str) -> Decimal:
        kind = _require_text(unit_kind, "unit_kind")
        if kind == "tokens":
            raise InvalidUsageArgument(
                "tokens use rate_in/rate_out; a single yuan_per_unit token rate is abolished"
            )
        key = _rate_key(model_id, kind)
        try:
            return self._rates[key]
        except KeyError:
            raise MissingRateCard(key[0], key[1]) from None

    def rate_in(self, model_id: str) -> Decimal:
        return self._token_yuan(_require_text(model_id, "model_id"), "in")

    def rate_out(self, model_id: str) -> Decimal:
        return self._token_yuan(_require_text(model_id, "model_id"), "out")

    def points_for(self, model_id: str, unit_kind: str, units: int | float | Decimal) -> int:
        kind = _require_text(unit_kind, "unit_kind")
        if kind == "tokens":
            raise InvalidUsageArgument(
                "tokens require points_for_tokens(input_units, output_units); "
                "single-leg / total-only token rates are abolished"
            )
        return compute_points(
            units,
            self.yuan_per_unit(model_id, kind),
            self.points_per_yuan,
        )

    def points_for_tokens(
        self,
        model_id: str,
        input_units: int | float | Decimal,
        output_units: int | float | Decimal,
    ) -> int:
        return compute_token_points(
            input_units,
            output_units,
            self.rate_in(model_id),
            self.rate_out(model_id),
            self.points_per_yuan,
        )

    def has_rate(self, model_id: str, unit_kind: str) -> bool:
        model = model_id.strip()
        kind = unit_kind.strip()
        if not model or kind not in BILLABLE_UNIT_KINDS:
            return False
        if kind == "tokens":
            return (model, "in") in self._token_rates and (model, "out") in self._token_rates
        return (model, kind) in self._rates

    def _token_yuan(self, model_id: str, direction: str) -> Decimal:
        try:
            return self._token_rates[(model_id, direction)]
        except KeyError:
            raise MissingRateCard(model_id, "tokens") from None

    def _ingest_card(self, card: StaticCard) -> None:
        model_id = _require_text(card.model_id, "model_id")
        if card.price_per_million_input is not None:
            self._token_rates[(model_id, "in")] = (
                _positive_yuan(card.price_per_million_input) / _MILLION
            )
        if card.price_per_million_output is not None:
            self._token_rates[(model_id, "out")] = (
                _positive_yuan(card.price_per_million_output) / _MILLION
            )
        if card.price_per_image_yuan is not None:
            self._rates[(model_id, "images")] = _positive_yuan(card.price_per_image_yuan)
        if card.price_per_clip_yuan is not None:
            self._rates[(model_id, "clips")] = _positive_yuan(card.price_per_clip_yuan)
        if card.price_per_1k_chars_yuan is not None:
            per_1k = _positive_yuan(card.price_per_1k_chars_yuan)
            self._rates[(model_id, "chars")] = per_1k / _THOUSAND

    def _ingest_rate_key(
        self,
        key: tuple[str, str] | tuple[str, str, str],
        value: Decimal | float | int,
    ) -> None:
        if not isinstance(key, tuple) or len(key) not in {2, 3}:
            raise InvalidUsageArgument(
                "rate key must be (model_id, unit_kind) or (model_id, 'tokens', 'in'|'out')"
            )
        if len(key) == 3:
            model_id, unit_kind, direction = key
            if _require_text(unit_kind, "unit_kind") != "tokens":
                raise InvalidUsageArgument("three-part rate keys are only for tokens")
            if direction not in _TOKEN_DIRECTIONS:
                raise InvalidUsageArgument("token direction must be 'in' or 'out'")
            self._token_rates[(_require_text(model_id, "model_id"), direction)] = _positive_yuan(
                value
            )
            return
        model_id, unit_kind = key
        if _require_text(unit_kind, "unit_kind") == "tokens":
            raise InvalidUsageArgument(
                "tokens require (model_id, 'tokens', 'in'|'out') keys; "
                "a single token rate is abolished"
            )
        self._rates[_rate_key(model_id, unit_kind)] = _positive_yuan(value)


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


def compute_token_points(
    input_units: int | float | Decimal,
    output_units: int | float | Decimal,
    yuan_per_input_unit: Decimal,
    yuan_per_output_unit: Decimal,
    points_per_yuan: int = POINTS_PER_YUAN,
) -> int:
    """ceil(in×rate_in + out×rate_out); one ceil over the sum. Min 1 when usage > 0.

    ``rate_in`` / ``rate_out`` are ``yuan_per_*_unit × POINTS_PER_YUAN``.
    """

    incoming = _as_decimal(input_units, "input_units")
    outgoing = _as_decimal(output_units, "output_units")
    if incoming < 0:
        raise InvalidUsageArgument("input_units must be >= 0")
    if outgoing < 0:
        raise InvalidUsageArgument("output_units must be >= 0")
    if incoming == 0 and outgoing == 0:
        return 0
    if yuan_per_input_unit <= 0 or yuan_per_output_unit <= 0:
        raise InvalidUsageArgument("token yuan_per_unit rates must be positive")
    raw = (incoming * yuan_per_input_unit + outgoing * yuan_per_output_unit) * Decimal(
        points_per_yuan
    )
    points = int(raw.to_integral_value(rounding=ROUND_CEILING))
    return max(1, points)


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
