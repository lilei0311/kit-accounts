"""Hub meter-event envelope → local points hold / settle / release.

联调冻结契约入站适配（账户积分侧）。This module parses the frozen
callback envelope and maps it onto ``PointsUsageSink``. It does **not**
start HTTP, authenticate callers, or implement an outbox.

Not a delivery claim:
- Hub hooks are still empty; there is no reliable push in this slice.
- ``event_id`` is transport identity only. Accounting keys remain
  ``request_id`` (+ ``kind`` for business-result dedupe).
- HTTP serving of ``POST /internal/v0/meter-events`` lives in
  ``maxkit_internal_http`` (local/staging shell only).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from maxkit_points import IdempotencyConflict
from maxkit_usage_bridge.errors import (
    BillingStateError,
    InvalidUsageArgument,
    MeterEnvelopeError,
)
from maxkit_usage_bridge.models import BillingMode, BillingResult, UsageEvent
from maxkit_usage_bridge.sink import PointsUsageSink

SCHEMA_VERSION = "0"
METER_EVENT_KINDS = ("task_hold", "task_settlement", "task_release")
MeterEventKind = Literal["task_hold", "task_settlement", "task_release"]

_EVENT_ID_PREFIX: dict[str, str] = {
    "task_hold": "evt_hold_",
    "task_settlement": "evt_settle_",
    "task_release": "evt_release_",
}
_DECIMAL_STRING = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d+)?$")
_HOLD_RESULTS = frozenset({"held", "rejected", "pending_config", "skipped"})
_SETTLE_RESULTS = frozenset({"settled", "shortfall"})
_RELEASE_RESULTS = frozenset({"released", "cancelled"})
_DURABLE_HTTP = frozenset({200, 422})


def expected_event_id(kind: str, request_id: str) -> str:
    prefix = _EVENT_ID_PREFIX.get(kind)
    if prefix is None:
        raise MeterEnvelopeError("invalid_payload", f"unsupported kind: {kind}")
    return f"{prefix}{request_id}"


@dataclass(frozen=True)
class UsageQuantity:
    model_id: str
    modality: str
    unit_kind: str
    units: Decimal | None = None
    input_units: Decimal | None = None
    output_units: Decimal | None = None
    estimated: bool | None = None

    def to_event(self) -> UsageEvent:
        return UsageEvent(
            model_id=self.model_id,
            modality=self.modality,
            units=self.units,
            unit_kind=self.unit_kind,
            input_units=self.input_units,
            output_units=self.output_units,
            estimated=self.estimated,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "modality": self.modality,
            "unit_kind": self.unit_kind,
            "units": None if self.units is None else format(self.units, "f"),
            "input_units": None if self.input_units is None else format(self.input_units, "f"),
            "output_units": None if self.output_units is None else format(self.output_units, "f"),
            "estimated": self.estimated,
        }


@dataclass(frozen=True)
class ParsedMeterEvent:
    schema_version: str
    event_id: str
    kind: MeterEventKind
    occurred_at: datetime
    request_id: str
    account_id: str
    billing: BillingMode
    payload_hash: str
    estimate: UsageQuantity | None = None
    meter: UsageQuantity | None = None
    reason: str | None = None
    snapshot_ref: str | None = None


@dataclass(frozen=True)
class MeterCallbackAck:
    """Local ACK after the points transaction commits (or validation fails).

    ``http_status`` / ``result`` / ``code`` follow the frozen callback table.
    This object is a library return value, not an HTTP response.
    """

    schema_version: str
    event_id: str
    request_id: str
    kind: str
    http_status: int
    result: str | None = None
    code: str | None = None
    original_result: str | None = None
    snapshot_ref: str | None = None
    effect: str | None = None
    retryable: bool = False
    skipped_reason: str | None = None
    reject_reason: str | None = None
    available_after: int | None = None
    billing_status: str | None = None
    detail: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "request_id": self.request_id,
            "kind": self.kind,
            "http_status": self.http_status,
        }
        if self.result is not None:
            payload["result"] = self.result
        if self.code is not None:
            payload["code"] = self.code
        if self.original_result is not None:
            payload["original_result"] = self.original_result
        if self.snapshot_ref is not None:
            payload["snapshot_ref"] = self.snapshot_ref
        if self.effect is not None:
            payload["effect"] = self.effect
        if self.retryable:
            payload["retryable"] = True
        return payload


class MeterEventAdapter:
    """Validate a frozen Hub envelope and drive ``PointsUsageSink``.

    In-process event log is transport dedupe only. Financial idempotency is
    still ``request_id`` on the points / billing-request store.
    """

    def __init__(self, sink: PointsUsageSink) -> None:
        self._sink = sink
        self._by_event: dict[str, tuple[str, str, MeterCallbackAck]] = {}

    def handle(self, envelope: object) -> MeterCallbackAck:
        if not isinstance(envelope, Mapping):
            return MeterCallbackAck(
                schema_version="",
                event_id="",
                request_id="",
                kind="",
                http_status=400,
                code="invalid_payload",
            )
        try:
            parsed = parse_meter_event(envelope)
        except MeterEnvelopeError as exc:
            return MeterCallbackAck(
                schema_version=_optional_str(envelope.get("schema_version")) or "",
                event_id=_optional_str(envelope.get("event_id")) or "",
                request_id=_optional_str(envelope.get("request_id")) or "",
                kind=_optional_str(envelope.get("kind")) or "",
                http_status=400,
                code=exc.code,
            )

        prior = self._by_event.get(parsed.event_id)
        if prior is not None:
            prior_hash, prior_request, prior_ack = prior
            if prior_request != parsed.request_id or prior_hash != parsed.payload_hash:
                return _error_ack(parsed, "payload_conflict", retryable=False)
            return _replay_ack(prior_ack)

        with self._sink.engine.transaction():
            ack = self._dispatch(parsed)
        if ack.http_status in _DURABLE_HTTP:
            self._by_event[parsed.event_id] = (parsed.payload_hash, parsed.request_id, ack)
        return ack

    def _dispatch(self, parsed: ParsedMeterEvent) -> MeterCallbackAck:
        try:
            if parsed.kind == "task_hold":
                return self._hold(parsed)
            if parsed.kind == "task_settlement":
                return self._settle(parsed)
            return self._release(parsed)
        except IdempotencyConflict:
            return _error_ack(parsed, "payload_conflict", retryable=False)
        except BillingStateError as exc:
            return self._from_state_error(parsed, exc)
        except InvalidUsageArgument as exc:
            if "snapshot_ref" in str(exc):
                return _error_ack(parsed, "payload_conflict", retryable=False)
            return MeterCallbackAck(
                schema_version=parsed.schema_version,
                event_id=parsed.event_id,
                request_id=parsed.request_id,
                kind=parsed.kind,
                http_status=400,
                code="invalid_payload",
            )

    def _hold(self, parsed: ParsedMeterEvent) -> MeterCallbackAck:
        assert parsed.estimate is not None
        existing = self._sink.get(parsed.request_id)
        if existing is not None and existing.account_id != parsed.account_id:
            return _error_ack(parsed, "payload_conflict", retryable=False)
        if existing is not None and existing.status in {"settled", "shortfall", "released"}:
            return _error_ack(parsed, "terminal_conflict", retryable=False)
        result = self._sink.hold(
            parsed.estimate.to_event(),
            account_id=parsed.account_id,
            request_id=parsed.request_id,
            billing=parsed.billing,
        )
        if result.status == "cancelled":
            return _ok_ack(parsed, "rejected", result, replay=False)
        replay = existing is not None and existing.status in _HOLD_RESULTS
        return _ok_ack(parsed, _business_result(result.status, parsed.kind), result, replay=replay)

    def _settle(self, parsed: ParsedMeterEvent) -> MeterCallbackAck:
        assert parsed.meter is not None
        existing = self._sink.get(parsed.request_id)
        if existing is None:
            if parsed.billing != "platform":
                result = self._sink.settle(
                    parsed.meter.to_event(),
                    account_id=parsed.account_id,
                    request_id=parsed.request_id,
                    billing=parsed.billing,
                    snapshot_ref=parsed.snapshot_ref,
                )
                return _ok_ack(parsed, "skipped", result, replay=False)
            return _error_ack(parsed, "predecessor_pending", retryable=True)
        if existing.account_id != parsed.account_id:
            return _error_ack(parsed, "payload_conflict", retryable=False)
        if existing.status in {"rejected", "cancelled", "released"}:
            return _error_ack(parsed, "terminal_conflict", retryable=False)
        if existing.status == "pending_config":
            return _error_ack(parsed, "rate_config_missing", http_status=422, retryable=False)
        result = self._sink.settle(
            parsed.meter.to_event(),
            account_id=parsed.account_id,
            request_id=parsed.request_id,
            billing=parsed.billing,
            snapshot_ref=parsed.snapshot_ref,
        )
        replay = existing.status in _SETTLE_RESULTS or (
            existing.status == "skipped" and result.status == "skipped"
        )
        return _ok_ack(parsed, _business_result(result.status, parsed.kind), result, replay=replay)

    def _release(self, parsed: ParsedMeterEvent) -> MeterCallbackAck:
        existing = self._sink.get(parsed.request_id)
        if existing is not None and existing.account_id != parsed.account_id:
            return _error_ack(parsed, "payload_conflict", retryable=False)
        if existing is not None and existing.status in {"settled", "shortfall", "rejected"}:
            return _error_ack(parsed, "terminal_conflict", retryable=False)
        if parsed.billing != "platform" and existing is None:
            return MeterCallbackAck(
                schema_version=parsed.schema_version,
                event_id=parsed.event_id,
                request_id=parsed.request_id,
                kind=parsed.kind,
                http_status=200,
                result="skipped",
                skipped_reason="byok",
                billing_status="skipped",
            )
        result = self._sink.release(
            account_id=parsed.account_id,
            request_id=parsed.request_id,
            snapshot_ref=parsed.snapshot_ref,
            reason=parsed.reason,
        )
        replay = existing is not None and existing.status in _RELEASE_RESULTS
        if existing is not None and existing.status == "skipped":
            replay = True
        return _ok_ack(parsed, _business_result(result.status, parsed.kind), result, replay=replay)

    def _from_state_error(
        self, parsed: ParsedMeterEvent, exc: BillingStateError
    ) -> MeterCallbackAck:
        if exc.status in {"missing", "pending_config"} and parsed.kind == "task_settlement":
            if exc.status == "pending_config":
                return _error_ack(parsed, "rate_config_missing", http_status=422, retryable=False)
            return _error_ack(parsed, "predecessor_pending", retryable=True)
        return _error_ack(parsed, "terminal_conflict", retryable=False)


def parse_meter_event(envelope: Mapping[str, Any]) -> ParsedMeterEvent:
    schema_version = _require_str(envelope.get("schema_version"), "schema_version")
    if schema_version != SCHEMA_VERSION:
        raise MeterEnvelopeError(
            "unsupported_schema",
            f"unsupported schema_version {schema_version}",
        )
    kind_raw = _require_str(envelope.get("kind"), "kind")
    if kind_raw not in METER_EVENT_KINDS:
        raise MeterEnvelopeError("invalid_payload", f"unsupported kind: {kind_raw}")
    kind: MeterEventKind = kind_raw  # type: ignore[assignment]
    request_id = _require_str(envelope.get("request_id"), "request_id")
    event_id = _require_str(envelope.get("event_id"), "event_id")
    expected = expected_event_id(kind, request_id)
    if event_id != expected:
        raise MeterEnvelopeError(
            "invalid_payload",
            f"event_id must be {expected} (transport identity; not an accounting key)",
        )
    occurred_at = _parse_occurred_at(envelope.get("occurred_at"))
    account_id = _require_str(envelope.get("account_id"), "account_id")
    billing = _require_str(envelope.get("billing"), "billing")
    if billing not in {"platform", "byok"}:
        raise MeterEnvelopeError("invalid_payload", "billing must be platform or byok")
    snapshot_ref = _optional_str(envelope.get("snapshot_ref"))
    estimate: UsageQuantity | None = None
    meter: UsageQuantity | None = None
    reason: str | None = None
    if kind == "task_hold":
        estimate = _parse_quantity(envelope.get("estimate"), "estimate")
    elif kind == "task_settlement":
        meter = _parse_quantity(envelope.get("meter"), "meter")
    else:
        reason = _optional_str(envelope.get("reason"))
    payload_hash = _payload_hash(
        request_id=request_id,
        account_id=account_id,
        billing=billing,
        kind=kind,
        estimate=estimate,
        meter=meter,
        reason=reason,
    )
    return ParsedMeterEvent(
        schema_version=schema_version,
        event_id=event_id,
        kind=kind,
        occurred_at=occurred_at,
        request_id=request_id,
        account_id=account_id,
        billing=billing,  # type: ignore[arg-type]
        payload_hash=payload_hash,
        estimate=estimate,
        meter=meter,
        reason=reason,
        snapshot_ref=snapshot_ref,
    )


def _parse_quantity(value: object, field: str) -> UsageQuantity:
    if not isinstance(value, Mapping):
        raise MeterEnvelopeError("invalid_payload", f"{field} must be an object")
    model_id = _require_str(value.get("model_id"), "model_id")
    modality = _require_str(value.get("modality"), "modality")
    unit_kind = _require_str(value.get("unit_kind"), "unit_kind")
    estimated = value.get("estimated", None)
    if estimated is not None and not isinstance(estimated, bool):
        raise MeterEnvelopeError("invalid_payload", "estimated must be a bool when set")
    has_units = "units" in value and value.get("units") is not None
    has_input = "input_units" in value and value.get("input_units") is not None
    has_output = "output_units" in value and value.get("output_units") is not None
    if unit_kind == "tokens":
        if has_units or not has_input or not has_output:
            raise MeterEnvelopeError(
                "invalid_payload",
                "tokens require input_units and output_units decimal strings; units is forbidden",
            )
        return UsageQuantity(
            model_id=model_id,
            modality=modality,
            unit_kind=unit_kind,
            input_units=_parse_decimal_string(value.get("input_units"), "input_units"),
            output_units=_parse_decimal_string(value.get("output_units"), "output_units"),
            estimated=estimated,
        )
    if not has_units or has_input or has_output:
        raise MeterEnvelopeError(
            "invalid_payload",
            f"{unit_kind} requires units decimal string; input_units/output_units are forbidden",
        )
    return UsageQuantity(
        model_id=model_id,
        modality=modality,
        unit_kind=unit_kind,
        units=_parse_decimal_string(value.get("units"), "units"),
        estimated=estimated,
    )


def _parse_decimal_string(value: object, field: str) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL_STRING.fullmatch(value):
        raise MeterEnvelopeError(
            "invalid_payload",
            f"{field} must be a non-negative finite decimal string",
        )
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise MeterEnvelopeError(
            "invalid_payload",
            f"{field} must be a non-negative finite decimal string",
        ) from exc
    if not amount.is_finite() or amount < 0:
        raise MeterEnvelopeError(
            "invalid_payload",
            f"{field} must be a non-negative finite decimal string",
        )
    return amount


def _parse_occurred_at(value: object) -> datetime:
    text = _require_str(value, "occurred_at")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MeterEnvelopeError("invalid_payload", "occurred_at must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MeterEnvelopeError("invalid_payload", "occurred_at must be timezone-aware")
    return parsed


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise MeterEnvelopeError("invalid_payload", f"{field} must be a string")
    text = value.strip()
    if not text:
        raise MeterEnvelopeError("invalid_payload", f"{field} must be non-empty")
    return text


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MeterEnvelopeError("invalid_payload", "optional string field has the wrong type")
    text = value.strip()
    return text or None


def _payload_hash(
    *,
    request_id: str,
    account_id: str,
    billing: str,
    kind: str,
    estimate: UsageQuantity | None,
    meter: UsageQuantity | None,
    reason: str | None,
) -> str:
    raw = json.dumps(
        {
            "request_id": request_id,
            "account_id": account_id,
            "billing": billing,
            "kind": kind,
            "estimate": None if estimate is None else estimate.as_dict(),
            "meter": None if meter is None else meter.as_dict(),
            "reason": reason,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _business_result(status: str, kind: str) -> str:
    if status == "held":
        return "applied"
    if status in {"settled", "released"}:
        return "applied"
    if status == "cancelled":
        return "applied" if kind == "task_release" else "rejected"
    if status in {"rejected", "shortfall", "skipped"}:
        return status
    if status == "pending_config":
        return "rate_config_missing"
    return status


def _ok_ack(
    parsed: ParsedMeterEvent,
    business: str,
    result: BillingResult,
    *,
    replay: bool,
) -> MeterCallbackAck:
    effect = _effect_name(parsed.kind, result)
    if business == "rate_config_missing":
        return MeterCallbackAck(
            schema_version=parsed.schema_version,
            event_id=parsed.event_id,
            request_id=parsed.request_id,
            kind=parsed.kind,
            http_status=422,
            code="rate_config_missing",
            snapshot_ref=result.snapshot_ref,
            skipped_reason=result.skipped_reason,
            available_after=result.available_after,
            billing_status=result.status,
            detail=None if result.effect is None else dict(result.effect),
        )
    shown = "duplicate" if replay else business
    original = business if replay else None
    return MeterCallbackAck(
        schema_version=parsed.schema_version,
        event_id=parsed.event_id,
        request_id=parsed.request_id,
        kind=parsed.kind,
        http_status=200,
        result=shown,
        original_result=original,
        snapshot_ref=result.snapshot_ref,
        effect=effect,
        skipped_reason=result.skipped_reason,
        reject_reason=result.reject_reason,
        available_after=result.available_after,
        billing_status=result.status,
        detail=None if result.effect is None else dict(result.effect),
    )


def _effect_name(kind: str, result: BillingResult) -> str | None:
    if kind == "task_release" and (
        result.status == "cancelled" or (result.effect or {}).get("outcome") == "cancelled"
    ):
        return "cancelled_without_hold"
    return None


def _error_ack(
    parsed: ParsedMeterEvent,
    code: str,
    *,
    retryable: bool,
    http_status: int = 409,
) -> MeterCallbackAck:
    return MeterCallbackAck(
        schema_version=parsed.schema_version,
        event_id=parsed.event_id,
        request_id=parsed.request_id,
        kind=parsed.kind,
        http_status=http_status,
        code=code,
        retryable=retryable,
    )


def _replay_ack(first: MeterCallbackAck) -> MeterCallbackAck:
    if first.http_status == 422:
        return first
    original = first.original_result or first.result
    return MeterCallbackAck(
        schema_version=first.schema_version,
        event_id=first.event_id,
        request_id=first.request_id,
        kind=first.kind,
        http_status=200,
        result="duplicate",
        original_result=original,
        snapshot_ref=first.snapshot_ref,
        effect=first.effect,
        skipped_reason=first.skipped_reason,
        reject_reason=first.reject_reason,
        available_after=first.available_after,
        billing_status=first.billing_status,
        detail=first.detail,
    )
