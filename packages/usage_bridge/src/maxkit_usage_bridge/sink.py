"""UsageEvent → points hold / settle / release.

The app injects this sink. The library does not start an HTTP gateway and
does not import kit-model-router. Ledger writes reuse consume-track
``task_hold`` / ``task_settlement`` / ``task_release``, plus formal
``usage_shortfall`` when a settle top-up cannot cover the remainder.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from threading import RLock

from maxkit_points import IdempotencyConflict, InsufficientPoints, PointsEngine
from maxkit_points.models import OwnerKind
from maxkit_usage_bridge.constants import BILLABLE_UNIT_KINDS, UNBILLED_UNIT_KINDS
from maxkit_usage_bridge.errors import (
    BillingStateError,
    InvalidUsageArgument,
    MissingRateCard,
)
from maxkit_usage_bridge.models import (
    BillingMode,
    BillingResult,
    BillingStatus,
    UsageEvent,
    UsageShortfall,
)
from maxkit_usage_bridge.rates import RateTable

_BILLING_MODES = ("platform", "byok")
_OWNER_KINDS = ("person", "org")


@dataclass
class _RequestState:
    request_id: str
    account_id: str
    payload_hash: str
    status: BillingStatus
    hold_ids: list[str] = field(default_factory=list)
    points: int = 0
    estimated: bool = False
    shortfall_points: int = 0
    skipped_reason: str | None = None
    model_id: str = ""
    unit_kind: str = ""


class PointsUsageSink:
    """Application-injected UsageSink: RateTable convert + points verbs.

    ``account_id`` / ``request_id`` come from the app. ``UsageEvent`` stays
    a pure usage record (model, modality, unit_kind, plus tokens I/O or
    non-token units). ``idempotency_key`` for the primary hold is
    ``request_id``.
    """

    def __init__(
        self,
        engine: PointsEngine,
        rates: RateTable,
        *,
        on_shortfall: Callable[[UsageShortfall], None] | None = None,
    ) -> None:
        self._engine = engine
        self._rates = rates
        self._on_shortfall = on_shortfall
        self._lock = RLock()
        self._requests: dict[str, _RequestState] = {}
        self.shortfalls: list[UsageShortfall] = []

    def hold(
        self,
        event: UsageEvent,
        *,
        account_id: str,
        request_id: str,
        billing: BillingMode,
        owner_kind: OwnerKind = "person",
    ) -> BillingResult:
        account = _require_text(account_id, "account_id")
        request = _require_text(request_id, "request_id")
        mode = _require_billing(billing)
        _require_owner_kind(owner_kind)
        model_id, unit_kind = _event_key(event)

        if mode != "platform":
            return self._skip(
                request,
                account,
                payload=_payload(
                    account_id=account,
                    billing=mode,
                    model_id=model_id,
                    unit_kind=unit_kind,
                    points=0,
                    estimated=False,
                    skipped_reason="byok",
                ),
                reason="byok",
                model_id=model_id,
                unit_kind=unit_kind,
            )
        if unit_kind in UNBILLED_UNIT_KINDS:
            return self._skip(
                request,
                account,
                payload=_payload(
                    account_id=account,
                    billing=mode,
                    model_id=model_id,
                    unit_kind=unit_kind,
                    points=0,
                    estimated=False,
                    skipped_reason="unbillable_unit",
                ),
                reason="unbillable_unit",
                model_id=model_id,
                unit_kind=unit_kind,
            )
        if unit_kind not in BILLABLE_UNIT_KINDS:
            raise MissingRateCard(model_id, unit_kind)

        quantity = _resolve_quantity(event)
        if quantity.points_units == 0:
            return self._skip(
                request,
                account,
                payload=_payload(
                    account_id=account,
                    billing=mode,
                    model_id=model_id,
                    unit_kind=unit_kind,
                    points=0,
                    estimated=False,
                    skipped_reason="no_usage",
                    input_units=quantity.input_units,
                    output_units=quantity.output_units,
                ),
                reason="no_usage",
                model_id=model_id,
                unit_kind=unit_kind,
            )
        if not self._rates.has_rate(model_id, unit_kind):
            raise MissingRateCard(model_id, unit_kind)

        points = self._points_for(event, quantity)
        payload = _payload(
            account_id=account,
            billing=mode,
            model_id=model_id,
            unit_kind=unit_kind,
            points=points,
            estimated=quantity.estimated,
            skipped_reason=None,
            input_units=quantity.input_units,
            output_units=quantity.output_units,
        )
        with self._lock:
            replay = self._replay(request, payload)
            if replay is not None:
                return self._result(replay)
            hold = self._engine.apply_hold(
                owner_id=account,
                amount=points,
                key=request,
                owner_kind=owner_kind,
                ref=request,
            )
            state = _RequestState(
                request_id=request,
                account_id=account,
                payload_hash=payload,
                status="held",
                hold_ids=[hold.id],
                points=points,
                estimated=quantity.estimated,
                model_id=model_id,
                unit_kind=unit_kind,
            )
            self._requests[request] = state
            return self._result(state)

    def settle(
        self,
        event: UsageEvent,
        *,
        account_id: str,
        request_id: str,
        billing: BillingMode,
        owner_kind: OwnerKind = "person",
    ) -> BillingResult:
        account = _require_text(account_id, "account_id")
        request = _require_text(request_id, "request_id")
        mode = _require_billing(billing)
        _require_owner_kind(owner_kind)
        model_id, unit_kind = _event_key(event)

        with self._lock:
            state = self._requests.get(request)
            if state is None:
                if mode != "platform" or unit_kind in UNBILLED_UNIT_KINDS:
                    return self._skip(
                        request,
                        account,
                        payload=_payload(
                            account_id=account,
                            billing=mode,
                            model_id=model_id,
                            unit_kind=unit_kind,
                            points=0,
                            estimated=False,
                            skipped_reason="byok" if mode != "platform" else "unbillable_unit",
                        ),
                        reason="byok" if mode != "platform" else "unbillable_unit",
                        model_id=model_id,
                        unit_kind=unit_kind,
                    )
                raise BillingStateError(request, "missing", "settle")
            if state.account_id != account:
                raise IdempotencyConflict(request)
            if state.status in {"settled", "shortfall", "skipped"}:
                return self._result(state)
            if state.status != "held":
                raise BillingStateError(request, state.status, "settle")

            actual_points, estimated = self._actual_points(event, state)
            if actual_points == 0:
                self._release_held(state)
                state.status = "released"
                state.points = 0
                state.estimated = estimated
                return self._result(state)
            if actual_points == state.points:
                for hold_id in state.hold_ids:
                    self._engine.apply_consume_settle(hold_id)
                state.status = "settled"
                state.estimated = state.estimated or estimated
                return self._result(state)
            if actual_points < state.points:
                return self._settle_less(state, actual_points, estimated, owner_kind)
            return self._settle_more(state, actual_points, estimated, owner_kind)

    def release(
        self,
        *,
        account_id: str,
        request_id: str,
    ) -> BillingResult:
        account = _require_text(account_id, "account_id")
        request = _require_text(request_id, "request_id")
        with self._lock:
            state = self._requests.get(request)
            if state is None:
                state = _RequestState(
                    request_id=request,
                    account_id=account,
                    payload_hash="",
                    status="skipped",
                    skipped_reason="missing",
                )
                self._requests[request] = state
                return self._result(state)
            if state.account_id != account:
                raise IdempotencyConflict(request)
            if state.status in {"released", "skipped"}:
                return self._result(state)
            if state.status != "held":
                raise BillingStateError(request, state.status, "release")
            self._release_held(state)
            state.status = "released"
            return self._result(state)

    def get(self, request_id: str) -> BillingResult | None:
        request = _require_text(request_id, "request_id")
        with self._lock:
            state = self._requests.get(request)
            return None if state is None else self._result(state)

    def _actual_points(self, event: UsageEvent, state: _RequestState) -> tuple[int, bool]:
        model_id, unit_kind = _event_key(event)
        if unit_kind == "tokens":
            quantity = _resolve_quantity(event)
            if quantity.points_units == 0:
                return 0, False
            if not self._rates.has_rate(model_id, unit_kind):
                raise MissingRateCard(model_id, unit_kind)
            return self._points_for(event, quantity), quantity.estimated
        if event.units is None:
            return state.points, state.estimated
        quantity = _resolve_quantity(event)
        if quantity.points_units == 0:
            return 0, False
        if not self._rates.has_rate(model_id, unit_kind):
            raise MissingRateCard(model_id, unit_kind)
        return self._points_for(event, quantity), quantity.estimated

    def _points_for(self, event: UsageEvent, quantity: _Quantity) -> int:
        model_id, unit_kind = _event_key(event)
        if unit_kind == "tokens":
            if quantity.input_units is None or quantity.output_units is None:
                raise InvalidUsageArgument("tokens require input_units and output_units")
            return self._rates.points_for_tokens(
                model_id, quantity.input_units, quantity.output_units
            )
        if quantity.units is None:
            raise InvalidUsageArgument("units must be a number")
        return self._rates.points_for(model_id, unit_kind, quantity.units)

    def _settle_less(
        self,
        state: _RequestState,
        actual_points: int,
        estimated: bool,
        owner_kind: OwnerKind,
    ) -> BillingResult:
        self._release_held(state)
        hold = self._engine.apply_hold(
            owner_id=state.account_id,
            amount=actual_points,
            key=f"{state.request_id}:settle",
            owner_kind=owner_kind,
            ref=state.request_id,
        )
        self._engine.apply_consume_settle(hold.id)
        state.hold_ids = [hold.id]
        state.points = actual_points
        state.estimated = estimated
        state.status = "settled"
        return self._result(state)

    def _settle_more(
        self,
        state: _RequestState,
        actual_points: int,
        estimated: bool,
        owner_kind: OwnerKind,
    ) -> BillingResult:
        needed = actual_points - state.points
        try:
            topup = self._engine.apply_hold(
                owner_id=state.account_id,
                amount=needed,
                key=f"{state.request_id}:topup",
                owner_kind=owner_kind,
                ref=state.request_id,
            )
        except InsufficientPoints:
            for hold_id in state.hold_ids:
                self._engine.apply_consume_settle(hold_id)
            self._write_shortfall_audit(state)
            shortfall = UsageShortfall(
                request_id=state.request_id,
                account_id=state.account_id,
                model_id=state.model_id,
                unit_kind=state.unit_kind,
                held_points=state.points,
                actual_points=actual_points,
                shortfall_points=needed,
            )
            state.status = "shortfall"
            state.shortfall_points = needed
            state.estimated = estimated
            self.shortfalls.append(shortfall)
            if self._on_shortfall is not None:
                self._on_shortfall(shortfall)
            return self._result(state)
        self._engine.apply_consume_settle(state.hold_ids[0])
        self._engine.apply_consume_settle(topup.id)
        state.hold_ids.append(topup.id)
        state.points = actual_points
        state.estimated = estimated
        state.status = "settled"
        return self._result(state)

    def _write_shortfall_audit(self, state: _RequestState) -> None:
        if not state.hold_ids:
            return
        hold = self._engine.get_hold(state.hold_ids[0])
        lot_id = hold.slices[0].lot_id if hold.slices else None
        if lot_id is None:
            return
        self._engine.record_usage_shortfall(
            owner_id=state.account_id,
            key=f"{state.request_id}:usage_shortfall",
            lot_id=lot_id,
            hold_id=hold.id,
            ref=state.request_id,
        )

    def _release_held(self, state: _RequestState) -> None:
        for hold_id in state.hold_ids:
            hold = self._engine.get_hold(hold_id)
            if hold.status == "held":
                self._engine.apply_release(hold_id)

    def _skip(
        self,
        request: str,
        account: str,
        *,
        payload: str,
        reason: str,
        model_id: str,
        unit_kind: str,
    ) -> BillingResult:
        with self._lock:
            replay = self._replay(request, payload)
            if replay is not None:
                return self._result(replay)
            state = _RequestState(
                request_id=request,
                account_id=account,
                payload_hash=payload,
                status="skipped",
                skipped_reason=reason,
                model_id=model_id,
                unit_kind=unit_kind,
            )
            self._requests[request] = state
            return self._result(state)

    def _replay(self, request_id: str, payload: str) -> _RequestState | None:
        existing = self._requests.get(request_id)
        if existing is None:
            return None
        if existing.payload_hash != payload:
            raise IdempotencyConflict(request_id)
        return existing

    def _result(self, state: _RequestState) -> BillingResult:
        return BillingResult(
            request_id=state.request_id,
            account_id=state.account_id,
            status=state.status,
            points=state.points,
            hold_ids=tuple(state.hold_ids),
            estimated=state.estimated,
            shortfall_points=state.shortfall_points,
            skipped_reason=state.skipped_reason,
            points_per_yuan=self._rates.points_per_yuan,
        )


def _event_key(event: UsageEvent) -> tuple[str, str]:
    if not isinstance(event, UsageEvent):
        raise InvalidUsageArgument("event must be a UsageEvent")
    return (
        _require_text(event.model_id, "model_id"),
        _require_text(event.unit_kind, "unit_kind"),
    )


@dataclass(frozen=True)
class _Quantity:
    units: Decimal | None
    input_units: Decimal | None
    output_units: Decimal | None
    estimated: bool

    @property
    def points_units(self) -> Decimal:
        if self.input_units is not None and self.output_units is not None:
            return self.input_units + self.output_units
        return self.units if self.units is not None else Decimal(0)


def _resolve_quantity(event: UsageEvent) -> _Quantity:
    if not isinstance(event, UsageEvent):
        raise InvalidUsageArgument("event must be a UsageEvent")
    if _require_text(event.unit_kind, "unit_kind") == "tokens":
        if event.input_units is None or event.output_units is None:
            raise InvalidUsageArgument(
                "tokens require input_units and output_units; "
                "total-only units and conservative max(in,out) rates are abolished"
            )
        incoming = _as_nonneg_decimal(event.input_units, "input_units")
        outgoing = _as_nonneg_decimal(event.output_units, "output_units")
        return _Quantity(
            units=None,
            input_units=incoming,
            output_units=outgoing,
            estimated=False,
        )
    if event.units is None:
        return _Quantity(units=Decimal(1), input_units=None, output_units=None, estimated=True)
    return _Quantity(
        units=_as_nonneg_decimal(event.units, "units"),
        input_units=None,
        output_units=None,
        estimated=False,
    )


def _as_nonneg_decimal(value: int | float | Decimal, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise InvalidUsageArgument(f"{field} must be a number")
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, int):
        amount = Decimal(value)
    else:
        amount = Decimal(str(value))
    if amount < 0:
        raise InvalidUsageArgument(f"{field} must be >= 0")
    return amount


def _require_billing(billing: str) -> BillingMode:
    if billing not in _BILLING_MODES:
        raise InvalidUsageArgument("billing must be platform or byok")
    return billing  # type: ignore[return-value]


def _require_owner_kind(owner_kind: str) -> None:
    if owner_kind not in _OWNER_KINDS:
        raise InvalidUsageArgument("owner_kind must be person or org")


def _require_text(value: str, field: str) -> str:
    text = value.strip()
    if not text:
        raise InvalidUsageArgument(f"{field} must be non-empty")
    return text


def _payload(
    *,
    account_id: str,
    billing: str,
    model_id: str,
    unit_kind: str,
    points: int,
    estimated: bool,
    skipped_reason: str | None,
    input_units: Decimal | None = None,
    output_units: Decimal | None = None,
) -> str:
    raw = json.dumps(
        {
            "account_id": account_id,
            "billing": billing,
            "model_id": model_id,
            "unit_kind": unit_kind,
            "points": points,
            "estimated": estimated,
            "skipped_reason": skipped_reason,
            "input_units": _json_num(input_units),
            "output_units": _json_num(output_units),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _json_num(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")
