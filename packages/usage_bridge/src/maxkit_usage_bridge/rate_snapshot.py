"""Immutable rate snapshot bound to a request at hold time.

Settlement and release load this local snapshot by ``request_id`` + verified
account. Live ``RateTable`` changes must not silently reprice an open hold.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from maxkit_usage_bridge.constants import POINTS_PER_YUAN
from maxkit_usage_bridge.errors import InvalidUsageArgument, MissingRateCard
from maxkit_usage_bridge.rates import RateTable, compute_points, compute_token_points


def freeze_rate_snapshot(
    rates: RateTable,
    *,
    model_id: str,
    unit_kind: str,
) -> dict[str, Any]:
    """Capture the rates used for this request. Raises MissingRateCard if absent."""

    model = _require_text(model_id, "model_id")
    kind = _require_text(unit_kind, "unit_kind")
    if not rates.has_rate(model, kind):
        raise MissingRateCard(model, kind)
    if kind == "tokens":
        payload = {
            "model_id": model,
            "unit_kind": kind,
            "points_per_yuan": rates.points_per_yuan,
            "yuan_per_input": _dec(rates.rate_in(model)),
            "yuan_per_output": _dec(rates.rate_out(model)),
        }
    else:
        payload = {
            "model_id": model,
            "unit_kind": kind,
            "points_per_yuan": rates.points_per_yuan,
            "yuan_per_unit": _dec(rates.yuan_per_unit(model, kind)),
        }
    payload["snapshot_ref"] = snapshot_ref_for(payload)
    return payload


def snapshot_ref_for(snapshot: dict[str, Any]) -> str:
    material = {key: value for key, value in snapshot.items() if key != "snapshot_ref"}
    raw = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def points_from_snapshot(
    snapshot: dict[str, Any],
    *,
    units: Decimal | None = None,
    input_units: Decimal | None = None,
    output_units: Decimal | None = None,
) -> int:
    """Price usage with a frozen snapshot. Does not consult a live RateTable."""

    if not isinstance(snapshot, dict):
        raise InvalidUsageArgument("rate_snapshot must be an object")
    kind = _require_text(str(snapshot.get("unit_kind", "")), "unit_kind")
    points_per_yuan = int(snapshot.get("points_per_yuan") or POINTS_PER_YUAN)
    if kind == "tokens":
        if input_units is None or output_units is None:
            raise InvalidUsageArgument("tokens require input_units and output_units")
        return compute_token_points(
            input_units,
            output_units,
            Decimal(str(snapshot["yuan_per_input"])),
            Decimal(str(snapshot["yuan_per_output"])),
            points_per_yuan,
        )
    if units is None:
        raise InvalidUsageArgument("units must be a number")
    return compute_points(
        units,
        Decimal(str(snapshot["yuan_per_unit"])),
        points_per_yuan,
    )


def verify_snapshot_ref(snapshot: dict[str, Any], snapshot_ref: str | None) -> None:
    """If Hub supplies snapshot_ref, it must match the local bound snapshot."""

    if snapshot_ref is None:
        return
    expected = snapshot.get("snapshot_ref") or snapshot_ref_for(snapshot)
    if snapshot_ref != expected:
        raise InvalidUsageArgument(
            f"snapshot_ref conflict: supplied {snapshot_ref!r} != local {expected!r}"
        )


def _dec(value: Decimal) -> str:
    return format(value, "f")


def _require_text(value: str, field: str) -> str:
    text = value.strip()
    if not text:
        raise InvalidUsageArgument(f"{field} must be non-empty")
    return text
