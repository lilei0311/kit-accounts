"""Local Hub→points inbound fixtures. No HTTP, no Hub push, no outbox."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from maxkit_points import ENTRY_KINDS, MemoryPointsStore, PointsEngine
from maxkit_usage_bridge import (
    MeterEventAdapter,
    PointsUsageSink,
    RateTable,
    expected_event_id,
    parse_meter_event,
)

REQUEST = "550e8400-e29b-41d4-a716-446655440000"
ACCOUNT = "acct_demo"
OCCURRED = "2026-11-09T13:00:00Z"


def _engine(amount: int = 1000) -> PointsEngine:
    engine = PointsEngine(
        store=MemoryPointsStore(),
        clock=lambda: datetime(2026, 11, 9, 13, tzinfo=UTC),
    )
    engine.apply_grant(owner_id=ACCOUNT, amount=amount, key="grant-demo")
    return engine


def _adapter(amount: int = 1000) -> tuple[PointsEngine, MeterEventAdapter]:
    engine = _engine(amount)
    sink = PointsUsageSink(engine, RateTable.from_snapshot())
    return engine, MeterEventAdapter(sink)


def _qty_tokens(input_units: str, output_units: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model_id": "demo-text-v0",
        "modality": "text",
        "unit_kind": "tokens",
        "input_units": input_units,
        "output_units": output_units,
    }
    body.update(extra)
    return body


def _envelope(kind: str, request_id: str = REQUEST, **fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": "0",
        "event_id": expected_event_id(kind, request_id),
        "kind": kind,
        "occurred_at": OCCURRED,
        "request_id": request_id,
        "account_id": ACCOUNT,
        "billing": "platform",
    }
    body.update(fields)
    return body


def _hold(**fields: Any) -> dict[str, Any]:
    fields.setdefault("estimate", _qty_tokens("100", "20"))
    return _envelope("task_hold", **fields)


def _settle(**fields: Any) -> dict[str, Any]:
    fields.setdefault("meter", _qty_tokens("80", "16", estimated=False))
    fields.setdefault("occurred_at", "2026-11-09T13:01:00Z")
    return _envelope("task_settlement", **fields)


def _release(**fields: Any) -> dict[str, Any]:
    fields.setdefault("reason", "hold_timeout")
    fields.setdefault("occurred_at", "2026-11-09T13:15:30Z")
    return _envelope("task_release", **fields)


def test_fixture_a_hold_applied_then_settle_omitting_snapshot_ref():
    engine, adapter = _adapter()
    held = adapter.handle(_hold())
    assert held.http_status == 200
    assert held.result == "applied"
    assert held.snapshot_ref
    assert held.billing_status == "held"
    assert engine.held_amount(ACCOUNT) > 0

    settled = adapter.handle(_settle())
    assert settled.http_status == 200
    assert settled.result == "applied"
    assert settled.billing_status == "settled"
    assert engine.held_amount(ACCOUNT) == 0
    assert engine.balances_match_ledger(ACCOUNT)
    refs = [entry.ref for entry in engine.entries(ACCOUNT) if entry.kind == "task_hold"]
    assert REQUEST in refs
    assert held.event_id not in refs


def test_fixture_b_hold_then_timeout_release():
    engine, adapter = _adapter()
    available = engine.available(ACCOUNT)
    held = adapter.handle(_hold())
    assert held.result == "applied"
    released = adapter.handle(_release())
    assert released.http_status == 200
    assert released.result == "applied"
    assert released.billing_status == "released"
    assert engine.available(ACCOUNT) == available
    assert engine.held_amount(ACCOUNT) == 0
    kinds = [entry.kind for entry in engine.entries(ACCOUNT)]
    assert "task_release" in kinds


def test_fixture_c_rejected_hold_blocks_later_settle_and_release():
    engine, adapter = _adapter(amount=1)
    rejected = adapter.handle(_hold(estimate=_qty_tokens("0", "100000")))
    assert rejected.http_status == 200
    assert rejected.result == "rejected"
    assert rejected.reject_reason == "insufficient_points"
    assert engine.held_amount(ACCOUNT) == 0
    available = engine.available(ACCOUNT)

    late_settle = adapter.handle(_settle(meter=_qty_tokens("0", "100000", estimated=False)))
    assert late_settle.http_status == 409
    assert late_settle.code == "terminal_conflict"
    assert late_settle.retryable is False
    assert late_settle.result is None

    late_release = adapter.handle(_release())
    assert late_release.http_status == 409
    assert late_release.code == "terminal_conflict"
    assert engine.available(ACCOUNT) == available
    assert engine.held_amount(ACCOUNT) == 0


def test_fixture_d_hold_event_id_replay_is_duplicate_with_original_snapshot():
    engine, adapter = _adapter()
    first = adapter.handle(_hold())
    assert first.result == "applied"
    available = engine.available(ACCOUNT)
    replay = adapter.handle(_hold())
    assert replay.http_status == 200
    assert replay.result == "duplicate"
    assert replay.original_result == "applied"
    assert replay.snapshot_ref == first.snapshot_ref
    assert engine.available(ACCOUNT) == available
    assert first.detail is not None
    assert engine.held_amount(ACCOUNT) == first.detail["points"]


def test_fixture_e_release_before_hold_tombstone_rejects_late_hold_without_refund():
    engine, adapter = _adapter()
    available = engine.available(ACCOUNT)
    cancelled = adapter.handle(_release())
    assert cancelled.http_status == 200
    assert cancelled.result == "applied"
    assert cancelled.effect == "cancelled_without_hold"
    assert cancelled.billing_status == "cancelled"
    assert engine.available(ACCOUNT) == available
    assert engine.held_amount(ACCOUNT) == 0
    kinds = [entry.kind for entry in engine.entries(ACCOUNT)]
    assert "task_release" not in kinds
    assert "task_hold" not in kinds

    late = adapter.handle(_hold())
    assert late.http_status == 200
    assert late.result == "rejected"
    assert engine.available(ACCOUNT) == available
    assert engine.held_amount(ACCOUNT) == 0
    assert [entry.kind for entry in engine.entries(ACCOUNT)].count("task_hold") == 0


def test_fixture_f_settle_shortfall_is_terminal_no_auto_topup():
    engine, adapter = _adapter(amount=5)
    held = adapter.handle(_hold(estimate=_qty_tokens("0", "5000")))
    assert held.result == "applied"
    short = adapter.handle(
        _settle(meter=_qty_tokens("0", "10000", estimated=False)),
    )
    assert short.http_status == 200
    assert short.result == "shortfall"
    assert short.billing_status == "shortfall"
    kinds = [entry.kind for entry in engine.entries(ACCOUNT)]
    assert "usage_shortfall" in ENTRY_KINDS
    assert kinds.count("usage_shortfall") == 1
    replay = adapter.handle(
        _settle(meter=_qty_tokens("0", "10000", estimated=False)),
    )
    assert replay.result == "duplicate"
    assert replay.original_result == "shortfall"
    assert [entry.kind for entry in engine.entries(ACCOUNT)].count("usage_shortfall") == 1
    late_release = adapter.handle(_release())
    assert late_release.code == "terminal_conflict"


def test_missing_rate_is_pending_config_not_permanent_reject():
    _, adapter = _adapter()
    pending = adapter.handle(
        _hold(
            estimate={
                "model_id": "no-card",
                "modality": "text",
                "unit_kind": "tokens",
                "input_units": "10",
                "output_units": "0",
            }
        )
    )
    assert pending.http_status == 422
    assert pending.code == "rate_config_missing"
    assert pending.billing_status == "pending_config"
    again = adapter.handle(
        _hold(
            estimate={
                "model_id": "no-card",
                "modality": "text",
                "unit_kind": "tokens",
                "input_units": "10",
                "output_units": "0",
            }
        )
    )
    assert again.http_status == 422
    assert again.code == "rate_config_missing"
    assert again.result is None


def test_byok_skips_persist_and_does_not_debit():
    engine, adapter = _adapter()
    skipped = adapter.handle(_hold(billing="byok"))
    assert skipped.http_status == 200
    assert skipped.result == "skipped"
    assert skipped.skipped_reason == "byok"
    assert engine.available(ACCOUNT) == 1000
    assert engine.held_amount(ACCOUNT) == 0
    replay = adapter.handle(_hold(billing="byok"))
    assert replay.result == "duplicate"
    assert replay.original_result == "skipped"
    assert engine.available(ACCOUNT) == 1000


def test_unordered_settle_is_predecessor_pending_then_applies_after_hold():
    engine, adapter = _adapter()
    pending = adapter.handle(_settle())
    assert pending.http_status == 409
    assert pending.code == "predecessor_pending"
    assert pending.retryable is True
    assert engine.held_amount(ACCOUNT) == 0
    held = adapter.handle(_hold())
    assert held.result == "applied"
    settled = adapter.handle(_settle())
    assert settled.result == "applied"
    assert settled.billing_status == "settled"


def test_wrong_explicit_snapshot_ref_conflicts_omitted_ok():
    _, adapter = _adapter()
    held = adapter.handle(_hold())
    conflict = adapter.handle(_settle(snapshot_ref="not-the-local-ref"))
    assert conflict.http_status == 409
    assert conflict.code == "payload_conflict"
    assert conflict.retryable is False
    ok = adapter.handle(_settle(snapshot_ref=held.snapshot_ref))
    assert ok.result == "applied"


def test_quantity_rules_reject_ambiguous_payloads():
    _, adapter = _adapter()
    tokens_with_units = _hold(
        estimate={
            "model_id": "demo-text-v0",
            "modality": "text",
            "unit_kind": "tokens",
            "units": "12",
            "input_units": "100",
            "output_units": "20",
        }
    )
    assert adapter.handle(tokens_with_units).code == "invalid_payload"
    missing_leg = _hold(
        estimate={
            "model_id": "demo-text-v0",
            "modality": "text",
            "unit_kind": "tokens",
            "input_units": "100",
        }
    )
    assert adapter.handle(missing_leg).code == "invalid_payload"
    images_with_io = _envelope(
        "task_settlement",
        meter={
            "model_id": "demo-image-v0",
            "modality": "image",
            "unit_kind": "images",
            "units": "3",
            "input_units": "1",
            "output_units": "0",
        },
    )
    assert adapter.handle(images_with_io).code == "invalid_payload"
    numeric = _hold(estimate=_qty_tokens(100, 20))  # type: ignore[arg-type]
    assert adapter.handle(numeric).code == "invalid_payload"
    nan = _hold(estimate=_qty_tokens("NaN", "1"))
    assert adapter.handle(nan).code == "invalid_payload"
    inf = _hold(estimate=_qty_tokens("Inf", "1"))
    assert adapter.handle(inf).code == "invalid_payload"
    negative = _hold(estimate=_qty_tokens("-1", "0"))
    assert adapter.handle(negative).code == "invalid_payload"


def test_unsupported_schema_and_non_deterministic_event_id_are_invalid():
    _, adapter = _adapter()
    bad_schema = _hold(schema_version="1")
    ack = adapter.handle(bad_schema)
    assert ack.http_status == 400
    assert ack.code == "unsupported_schema"
    mutated_id = _hold()
    mutated_id["event_id"] = "evt_other_" + REQUEST
    ack_id = adapter.handle(mutated_id)
    assert ack_id.http_status == 400
    assert ack_id.code == "invalid_payload"


def test_occurred_at_is_not_a_financial_key():
    parsed_a = parse_meter_event(_hold(occurred_at="2026-11-09T13:00:00Z"))
    parsed_b = parse_meter_event(_hold(occurred_at="2026-11-09T18:00:00Z"))
    assert parsed_a.payload_hash == parsed_b.payload_hash
    assert parsed_a.occurred_at != parsed_b.occurred_at


def test_images_settle_happy_path_uses_units_only():
    engine, adapter = _adapter()
    hold = adapter.handle(
        _hold(
            estimate={
                "model_id": "demo-image-v0",
                "modality": "image",
                "unit_kind": "images",
                "units": "3",
            }
        )
    )
    assert hold.result == "applied"
    settled = adapter.handle(
        _settle(
            meter={
                "model_id": "demo-image-v0",
                "modality": "image",
                "unit_kind": "images",
                "units": "3",
                "estimated": False,
            }
        )
    )
    assert settled.result == "applied"
    assert engine.held_amount(ACCOUNT) == 0


def test_callback_doc_states_draft_contract_not_delivery():
    from pathlib import Path

    root = next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "pyproject.toml").exists() and (parent / "packages").exists()
    )
    doc = (root / "docs" / "hub-points-callback.md").read_text(encoding="utf-8")
    assert "不是真投递" in doc
    assert "outbox" in doc
    assert "POST /internal/v0/meter-events" in doc
    assert "event_id" in doc and "request_id" in doc
    assert "MeterEventAdapter" in doc
    assert "cancelled_without_hold" in doc
    assert "predecessor_pending" in doc


def test_adapter_does_not_import_meter_or_router():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    imports = [
        line.strip() for line in text.splitlines() if line.lstrip().startswith(("import ", "from "))
    ]
    joined = "\n".join(imports).lower()
    assert "kit-meter" not in joined
    assert "kit_meter" not in joined
    assert "kit-model-router" not in joined
    assert "kit_model_router" not in joined
    assert "fastapi" not in joined
    assert "starlette" not in joined
