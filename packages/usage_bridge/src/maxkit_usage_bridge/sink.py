"""UsageEvent → points hold / settle / release.

The app injects this sink. The library does not start an HTTP gateway and
does not import kit-model-router. Ledger writes reuse consume-track
``task_hold`` / ``task_settlement`` / ``task_release``, plus formal
``usage_shortfall`` when a settle top-up cannot cover the remainder.

Request lifecycle is keyed solely by immutable ``request_id``. Transport
event IDs are ignored for accounting. Hold freezes a local rate snapshot;
settle/release price from that snapshot (or conflict if an explicit wrong
``snapshot_ref`` is supplied). Release-before-hold writes a cancellation
tombstone so a late hold cannot debit.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from maxkit_points import IdempotencyConflict, InsufficientPoints, PointsEngine
from maxkit_points.billing_request import BillingRequest, BillingRequestStore
from maxkit_points.models import OwnerKind
from maxkit_usage_bridge.constants import BILLABLE_UNIT_KINDS, UNBILLED_UNIT_KINDS
from maxkit_usage_bridge.errors import BillingStateError, InvalidUsageArgument
from maxkit_usage_bridge.models import (
    BillingMode,
    BillingResult,
    BillingStatus,
    UsageEvent,
    UsageShortfall,
)
from maxkit_usage_bridge.rate_snapshot import (
    freeze_rate_snapshot,
    points_from_snapshot,
    verify_snapshot_ref,
)
from maxkit_usage_bridge.rates import RateTable

_BILLING_MODES = ("platform", "byok")
_OWNER_KINDS = ("person", "org")
_REPLAYABLE = frozenset(
    {
        "pending_config",
        "held",
        "settled",
        "released",
        "cancelled",
        "rejected",
        "shortfall",
        "skipped",
    }
)


class PointsUsageSink:
    """Application-injected UsageSink: RateTable convert + points verbs.

    ``account_id`` / ``request_id`` come from the app. ``UsageEvent`` stays
    a pure usage record. ``idempotency_key`` for the primary hold is
    ``request_id``. Billing request rows are stored on the engine store when
    it implements ``BillingRequestStore`` (MemoryPointsStore and
    PostgresPointsStore both do).
    """

    def __init__(
        self,
        engine: PointsEngine,
        rates: RateTable,
        *,
        on_shortfall: Callable[[UsageShortfall], None] | None = None,
        requests: BillingRequestStore | None = None,
    ) -> None:
        self._engine = engine
        self._rates = rates
        self._on_shortfall = on_shortfall
        store = engine.store
        if requests is not None:
            self._requests = requests
        elif hasattr(store, "save_billing_request"):
            self._requests = store  # type: ignore[assignment]
        else:
            raise InvalidUsageArgument(
                "engine store must implement BillingRequestStore "
                "(MemoryPointsStore / PostgresPointsStore) or pass requests="
            )

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
        now = datetime.now(UTC)

        with self._engine.transaction():
            existing = self._requests.lock_billing_request(request)
            if existing is not None:
                self._assert_account(existing, account)
                if existing.status == "cancelled":
                    return self._result(existing)
                if existing.status in _REPLAYABLE and existing.payload_hash:
                    # Intent checked below after payload is known for platform holds.
                    pass

            if mode != "platform":
                return self._persist_skip(
                    request,
                    account,
                    existing=existing,
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
                    billing=mode,
                    now=now,
                )
            if unit_kind in UNBILLED_UNIT_KINDS:
                return self._persist_skip(
                    request,
                    account,
                    existing=existing,
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
                    billing=mode,
                    now=now,
                )
            if unit_kind not in BILLABLE_UNIT_KINDS:
                return self._persist_pending_or_conflict(
                    request,
                    account,
                    existing=existing,
                    payload=_payload(
                        account_id=account,
                        billing=mode,
                        model_id=model_id,
                        unit_kind=unit_kind,
                        points=0,
                        estimated=False,
                        skipped_reason="missing_rate",
                    ),
                    model_id=model_id,
                    unit_kind=unit_kind,
                    billing=mode,
                    now=now,
                )

            quantity = _resolve_quantity(event)
            if quantity.points_units == 0:
                return self._persist_skip(
                    request,
                    account,
                    existing=existing,
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
                    billing=mode,
                    now=now,
                )

            if not self._rates.has_rate(model_id, unit_kind):
                return self._persist_pending_or_conflict(
                    request,
                    account,
                    existing=existing,
                    payload=_payload(
                        account_id=account,
                        billing=mode,
                        model_id=model_id,
                        unit_kind=unit_kind,
                        points=0,
                        estimated=quantity.estimated,
                        skipped_reason="missing_rate",
                        input_units=quantity.input_units,
                        output_units=quantity.output_units,
                    ),
                    model_id=model_id,
                    unit_kind=unit_kind,
                    billing=mode,
                    now=now,
                )

            rate_snapshot = freeze_rate_snapshot(
                self._rates, model_id=model_id, unit_kind=unit_kind
            )
            points = self._points_for_live(event, quantity)
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
            if existing is not None:
                replay = self._replay_hold(existing, payload)
                if replay is not None:
                    return replay

            try:
                hold = self._engine.apply_hold(
                    owner_id=account,
                    amount=points,
                    key=request,
                    owner_kind=owner_kind,
                    ref=request,
                )
            except InsufficientPoints as exc:
                state = BillingRequest(
                    request_id=request,
                    account_id=account,
                    status="rejected",
                    payload_hash=payload,
                    rate_snapshot=rate_snapshot,
                    snapshot_ref=rate_snapshot["snapshot_ref"],
                    points=points,
                    estimated=quantity.estimated,
                    reject_reason="insufficient_points",
                    model_id=model_id,
                    unit_kind=unit_kind,
                    billing=mode,
                    available_after=exc.available,
                    effect={
                        "operation": "hold",
                        "outcome": "rejected",
                        "requested": points,
                        "available": exc.available,
                    },
                    created_at=now,
                    updated_at=now,
                )
                self._requests.save_billing_request(state)
                return self._result(state)

            available = self._engine.available(account)
            state = BillingRequest(
                request_id=request,
                account_id=account,
                status="held",
                payload_hash=payload,
                rate_snapshot=rate_snapshot,
                snapshot_ref=rate_snapshot["snapshot_ref"],
                hold_ids=[hold.id],
                points=points,
                estimated=quantity.estimated,
                model_id=model_id,
                unit_kind=unit_kind,
                billing=mode,
                available_after=available,
                effect={
                    "operation": "hold",
                    "outcome": "held",
                    "points": points,
                    "hold_ids": [hold.id],
                    "available": available,
                },
                created_at=now,
                updated_at=now,
            )
            self._requests.save_billing_request(state)
            return self._result(state)

    def settle(
        self,
        event: UsageEvent,
        *,
        account_id: str,
        request_id: str,
        billing: BillingMode,
        owner_kind: OwnerKind = "person",
        snapshot_ref: str | None = None,
    ) -> BillingResult:
        account = _require_text(account_id, "account_id")
        request = _require_text(request_id, "request_id")
        mode = _require_billing(billing)
        _require_owner_kind(owner_kind)
        model_id, unit_kind = _event_key(event)
        now = datetime.now(UTC)

        with self._engine.transaction():
            state = self._requests.lock_billing_request(request)
            if state is None:
                if mode != "platform" or unit_kind in UNBILLED_UNIT_KINDS:
                    return self._persist_skip(
                        request,
                        account,
                        existing=None,
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
                        billing=mode,
                        now=now,
                    )
                # Missing-hold settle must not guess a rate.
                raise BillingStateError(request, "missing", "settle")
            self._assert_account(state, account)
            if state.status in {"settled", "shortfall", "skipped", "cancelled", "rejected"}:
                return self._result(state)
            if state.status == "pending_config":
                raise BillingStateError(request, state.status, "settle")
            if state.status == "released":
                raise BillingStateError(request, state.status, "settle")
            if state.status != "held":
                raise BillingStateError(request, state.status, "settle")
            if state.rate_snapshot is None:
                raise BillingStateError(request, "missing_rate_snapshot", "settle")
            verify_snapshot_ref(state.rate_snapshot, snapshot_ref)

            actual_points, estimated = self._actual_points_from_snapshot(event, state)
            settle_hash = _payload(
                account_id=account,
                billing=mode,
                model_id=model_id,
                unit_kind=unit_kind,
                points=actual_points,
                estimated=estimated,
                skipped_reason=None,
                input_units=_quantity_io(event)[0],
                output_units=_quantity_io(event)[1],
            )
            if state.settle_payload_hash and state.settle_payload_hash != settle_hash:
                raise IdempotencyConflict(request)
            state.settle_payload_hash = settle_hash

            if actual_points == 0:
                self._release_held(state)
                state.status = "released"
                state.points = 0
                state.estimated = estimated
                state.available_after = self._engine.available(account)
                state.effect = {
                    "operation": "settle",
                    "outcome": "released",
                    "points": 0,
                    "available": state.available_after,
                }
                state.updated_at = now
                self._requests.save_billing_request(state)
                return self._result(state)
            if actual_points == state.points:
                for hold_id in state.hold_ids:
                    self._engine.apply_consume_settle(hold_id)
                state.status = "settled"
                state.estimated = state.estimated or estimated
                state.available_after = self._engine.available(account)
                state.effect = {
                    "operation": "settle",
                    "outcome": "settled",
                    "points": state.points,
                    "hold_ids": list(state.hold_ids),
                    "available": state.available_after,
                }
                state.updated_at = now
                self._requests.save_billing_request(state)
                return self._result(state)
            if actual_points < state.points:
                return self._settle_less(state, actual_points, estimated, owner_kind, now)
            return self._settle_more(state, actual_points, estimated, owner_kind, now)

    def release(
        self,
        *,
        account_id: str,
        request_id: str,
        snapshot_ref: str | None = None,
        reason: str | None = None,
    ) -> BillingResult:
        account = _require_text(account_id, "account_id")
        request = _require_text(request_id, "request_id")
        now = datetime.now(UTC)
        with self._engine.transaction():
            state = self._requests.lock_billing_request(request)
            if state is None:
                # Authoritative release before any applied hold: cancellation
                # tombstone, no refund, late hold cannot debit.
                state = BillingRequest(
                    request_id=request,
                    account_id=account,
                    status="cancelled",
                    payload_hash="",
                    skipped_reason=reason or "release_before_hold",
                    reject_reason=None,
                    available_after=self._engine.available(account),
                    effect={
                        "operation": "release",
                        "outcome": "cancelled",
                        "reason": reason or "release_before_hold",
                        "available": self._engine.available(account),
                    },
                    created_at=now,
                    updated_at=now,
                )
                self._requests.save_billing_request(state)
                return self._result(state)
            self._assert_account(state, account)
            if state.status in {"released", "cancelled", "skipped", "rejected"}:
                return self._result(state)
            if state.status in {"settled", "shortfall"}:
                raise BillingStateError(request, state.status, "release")
            if state.status == "pending_config":
                state.status = "cancelled"
                state.skipped_reason = reason or "release_before_hold"
                state.available_after = self._engine.available(account)
                state.effect = {
                    "operation": "release",
                    "outcome": "cancelled",
                    "reason": state.skipped_reason,
                    "available": state.available_after,
                }
                state.updated_at = now
                self._requests.save_billing_request(state)
                return self._result(state)
            if state.status != "held":
                raise BillingStateError(request, state.status, "release")
            if state.rate_snapshot is not None:
                verify_snapshot_ref(state.rate_snapshot, snapshot_ref)
            self._release_held(state)
            state.status = "released"
            state.available_after = self._engine.available(account)
            state.effect = {
                "operation": "release",
                "outcome": "released",
                "reason": reason,
                "points_restored": state.points,
                "available": state.available_after,
            }
            state.updated_at = now
            self._requests.save_billing_request(state)
            return self._result(state)

    def get(self, request_id: str) -> BillingResult | None:
        request = _require_text(request_id, "request_id")
        with self._engine.transaction():
            state = self._requests.get_billing_request(request)
            return None if state is None else self._result(state)

    def _actual_points_from_snapshot(
        self, event: UsageEvent, state: BillingRequest
    ) -> tuple[int, bool]:
        assert state.rate_snapshot is not None
        model_id, unit_kind = _event_key(event)
        if unit_kind == "tokens":
            quantity = _resolve_quantity(event)
            if quantity.points_units == 0:
                return 0, _estimated_flag(event, quantity.estimated)
            points = points_from_snapshot(
                state.rate_snapshot,
                input_units=quantity.input_units,
                output_units=quantity.output_units,
            )
            return points, _estimated_flag(event, quantity.estimated)
        if event.units is None:
            return state.points, state.estimated
        quantity = _resolve_quantity(event)
        if quantity.points_units == 0:
            return 0, False
        points = points_from_snapshot(state.rate_snapshot, units=quantity.units)
        return points, _estimated_flag(event, quantity.estimated)

    def _points_for_live(self, event: UsageEvent, quantity: _Quantity) -> int:
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
        state: BillingRequest,
        actual_points: int,
        estimated: bool,
        owner_kind: OwnerKind,
        now: datetime,
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
        state.available_after = self._engine.available(state.account_id)
        state.effect = {
            "operation": "settle",
            "outcome": "settled",
            "points": actual_points,
            "hold_ids": list(state.hold_ids),
            "available": state.available_after,
            "adjustment": "refund_excess",
        }
        state.updated_at = now
        self._requests.save_billing_request(state)
        return self._result(state)

    def _settle_more(
        self,
        state: BillingRequest,
        actual_points: int,
        estimated: bool,
        owner_kind: OwnerKind,
        now: datetime,
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
            state.available_after = self._engine.available(state.account_id)
            state.effect = {
                "operation": "settle",
                "outcome": "shortfall",
                "points": state.points,
                "shortfall_points": needed,
                "actual_points": actual_points,
                "hold_ids": list(state.hold_ids),
                "available": state.available_after,
            }
            state.updated_at = now
            self._requests.save_billing_request(state)
            if self._on_shortfall is not None:
                self._on_shortfall(shortfall)
            return self._result(state)
        self._engine.apply_consume_settle(state.hold_ids[0])
        self._engine.apply_consume_settle(topup.id)
        state.hold_ids.append(topup.id)
        state.points = actual_points
        state.estimated = estimated
        state.status = "settled"
        state.available_after = self._engine.available(state.account_id)
        state.effect = {
            "operation": "settle",
            "outcome": "settled",
            "points": actual_points,
            "hold_ids": list(state.hold_ids),
            "available": state.available_after,
            "adjustment": "topup",
        }
        state.updated_at = now
        self._requests.save_billing_request(state)
        return self._result(state)

    def _write_shortfall_audit(self, state: BillingRequest) -> None:
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

    def _release_held(self, state: BillingRequest) -> None:
        for hold_id in state.hold_ids:
            hold = self._engine.get_hold(hold_id)
            if hold.status == "held":
                self._engine.apply_release(hold_id)

    def _persist_skip(
        self,
        request: str,
        account: str,
        *,
        existing: BillingRequest | None,
        payload: str,
        reason: str,
        model_id: str,
        unit_kind: str,
        billing: str,
        now: datetime,
    ) -> BillingResult:
        if existing is not None:
            self._assert_account(existing, account)
            if existing.status == "cancelled":
                return self._result(existing)
            if existing.payload_hash and existing.payload_hash != payload:
                raise IdempotencyConflict(request)
            if existing.status in _REPLAYABLE:
                return self._result(existing)
        available = self._engine.available(account)
        state = BillingRequest(
            request_id=request,
            account_id=account,
            status="skipped",
            payload_hash=payload,
            skipped_reason=reason,
            model_id=model_id,
            unit_kind=unit_kind,
            billing=billing,
            available_after=available,
            effect={
                "operation": "hold",
                "outcome": "skipped",
                "reason": reason,
                "available": available,
            },
            created_at=now,
            updated_at=now,
        )
        self._requests.save_billing_request(state)
        return self._result(state)

    def _persist_pending_or_conflict(
        self,
        request: str,
        account: str,
        *,
        existing: BillingRequest | None,
        payload: str,
        model_id: str,
        unit_kind: str,
        billing: str,
        now: datetime,
    ) -> BillingResult:
        if existing is not None:
            self._assert_account(existing, account)
            if existing.status == "cancelled":
                return self._result(existing)
            if existing.payload_hash and existing.payload_hash != payload:
                raise IdempotencyConflict(request)
            if existing.status == "pending_config":
                return self._result(existing)
            if existing.status in _REPLAYABLE:
                return self._result(existing)
        available = self._engine.available(account)
        state = BillingRequest(
            request_id=request,
            account_id=account,
            status="pending_config",
            payload_hash=payload,
            skipped_reason="missing_rate",
            model_id=model_id,
            unit_kind=unit_kind,
            billing=billing,
            available_after=available,
            effect={
                "operation": "hold",
                "outcome": "pending_config",
                "available": available,
            },
            created_at=now,
            updated_at=now,
        )
        self._requests.save_billing_request(state)
        return self._result(state)

    def _replay_hold(self, existing: BillingRequest, payload: str) -> BillingResult | None:
        if existing.status == "cancelled":
            return self._result(existing)
        if existing.payload_hash and existing.payload_hash != payload:
            raise IdempotencyConflict(existing.request_id)
        if existing.status in _REPLAYABLE and existing.payload_hash:
            return self._result(existing)
        return None

    def _assert_account(self, state: BillingRequest, account: str) -> None:
        if state.account_id != account:
            raise IdempotencyConflict(state.request_id)

    def _result(self, state: BillingRequest) -> BillingResult:
        status: BillingStatus = state.status  # type: ignore[assignment]
        return BillingResult(
            request_id=state.request_id,
            account_id=state.account_id,
            status=status,
            points=state.points,
            hold_ids=tuple(state.hold_ids),
            estimated=state.estimated,
            shortfall_points=state.shortfall_points,
            skipped_reason=state.skipped_reason,
            points_per_yuan=self._rates.points_per_yuan,
            reject_reason=state.reject_reason,
            available_after=state.available_after,
            snapshot_ref=state.snapshot_ref,
            effect=dict(state.effect),
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
            estimated=_estimated_flag(event, False),
        )
    if event.units is None:
        return _Quantity(
            units=Decimal(1),
            input_units=None,
            output_units=None,
            estimated=_estimated_flag(event, True),
        )
    return _Quantity(
        units=_as_nonneg_decimal(event.units, "units"),
        input_units=None,
        output_units=None,
        estimated=_estimated_flag(event, False),
    )


def _estimated_flag(event: UsageEvent, default: bool) -> bool:
    if event.estimated is None:
        return default
    if not isinstance(event.estimated, bool):
        raise InvalidUsageArgument("estimated must be a bool when set")
    return event.estimated


def _quantity_io(event: UsageEvent) -> tuple[Decimal | None, Decimal | None]:
    if event.unit_kind == "tokens":
        quantity = _resolve_quantity(event)
        return quantity.input_units, quantity.output_units
    return None, None


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
