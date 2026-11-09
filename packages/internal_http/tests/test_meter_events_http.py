"""HTTP inbound around MeterEventAdapter. Auth, validation, applied-after-commit."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from starlette.testclient import TestClient

from maxkit_internal_http import (
    CREDENTIAL_ENV,
    LOCAL_DEV_CREDENTIAL_ID,
    LOCAL_DEV_TOKEN,
    METER_EVENTS_PATH,
    TOKEN_ENV,
    MeterHttpConfigError,
    MeterHttpSettings,
    create_app,
)
from maxkit_internal_http.http import DEMO_ACCOUNT_ID
from maxkit_points import MemoryPointsStore, PointsEngine
from maxkit_usage_bridge import MeterEventAdapter, PointsUsageSink, RateTable, expected_event_id

REQUEST = "550e8400-e29b-41d4-a716-446655440000"
OCCURRED = "2026-11-09T13:00:00Z"
AUTH_HEADERS = {
    "Authorization": f"Bearer {LOCAL_DEV_TOKEN}",
    "credential_id": LOCAL_DEV_CREDENTIAL_ID,
}


class CommitRecordingStore(MemoryPointsStore):
    """Records outermost successful transaction commit for HTTP ordering tests."""

    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self._events = events

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            with super().transaction():
                yield
        except Exception:
            raise
        else:
            if self._depth == 0:
                self._events.append("commit")


def _settings(**overrides: str) -> MeterHttpSettings:
    return MeterHttpSettings(
        token=overrides.get("token", LOCAL_DEV_TOKEN),
        credential_id=overrides.get("credential_id", LOCAL_DEV_CREDENTIAL_ID),
    )


def _hold_envelope(**fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": "0",
        "event_id": expected_event_id("task_hold", REQUEST),
        "kind": "task_hold",
        "occurred_at": OCCURRED,
        "request_id": REQUEST,
        "account_id": DEMO_ACCOUNT_ID,
        "billing": "platform",
        "estimate": {
            "model_id": "demo-text-v0",
            "modality": "text",
            "unit_kind": "tokens",
            "input_units": "100",
            "output_units": "20",
        },
    }
    body.update(fields)
    return body


def _runtime(events: list[str] | None = None) -> tuple[PointsEngine, MeterEventAdapter]:
    store = CommitRecordingStore(events) if events is not None else MemoryPointsStore()
    engine = PointsEngine(
        store=store,
        clock=lambda: datetime(2026, 11, 9, 13, tzinfo=UTC),
    )
    engine.apply_grant(owner_id=DEMO_ACCOUNT_ID, amount=1000, key="grant-demo")
    if events is not None:
        events.clear()
    sink = PointsUsageSink(engine, RateTable.from_snapshot())
    return engine, MeterEventAdapter(sink)


def _order_client(events: list[str], app: Any) -> TestClient:
    async def wrapped(scope: Any, receive: Any, send: Any) -> None:
        async def send_wrapper(message: Any) -> None:
            if message["type"] == "http.response.start":
                events.append(f"respond:{message['status']}")
            await send(message)

        await app(scope, receive, send_wrapper)

    return TestClient(wrapped)


def test_missing_authorization_is_401():
    _engine, adapter = _runtime()
    client = TestClient(create_app(adapter=adapter, settings=_settings()))
    response = client.post(
        METER_EVENTS_PATH,
        json=_hold_envelope(),
        headers={"credential_id": LOCAL_DEV_CREDENTIAL_ID},
    )
    assert response.status_code == 401
    assert response.json() == {"code": "service_auth_invalid"}
    assert _engine.held_amount(DEMO_ACCOUNT_ID) == 0


def test_wrong_credential_id_is_403():
    _engine, adapter = _runtime()
    client = TestClient(create_app(adapter=adapter, settings=_settings()))
    response = client.post(
        METER_EVENTS_PATH,
        json=_hold_envelope(),
        headers={
            "Authorization": f"Bearer {LOCAL_DEV_TOKEN}",
            "credential_id": "cred_wrong",
        },
    )
    assert response.status_code == 403
    assert response.json() == {"code": "service_forbidden"}


def test_invalid_envelope_is_400_before_persist():
    engine, adapter = _runtime()
    client = TestClient(create_app(adapter=adapter, settings=_settings()))
    bad = _hold_envelope()
    bad["estimate"]["input_units"] = 100
    response = client.post(METER_EVENTS_PATH, json=bad, headers=AUTH_HEADERS)
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "invalid_payload"
    assert body["schema_version"] == "0"
    assert body["event_id"] == expected_event_id("task_hold", REQUEST)
    assert body["request_id"] == REQUEST
    assert body["kind"] == "task_hold"
    assert "result" not in body
    assert engine.held_amount(DEMO_ACCOUNT_ID) == 0
    refs = [entry.ref for entry in engine.entries(DEMO_ACCOUNT_ID)]
    assert REQUEST not in refs


def test_hold_applied_commits_before_http_200():
    events: list[str] = []
    engine, adapter = _runtime(events)
    app = create_app(adapter=adapter, settings=_settings())
    client = _order_client(events, app)
    response = client.post(METER_EVENTS_PATH, json=_hold_envelope(), headers=AUTH_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "applied"
    assert body["schema_version"] == "0"
    assert body["event_id"] == expected_event_id("task_hold", REQUEST)
    assert body["request_id"] == REQUEST
    assert body["kind"] == "task_hold"
    assert body["snapshot_ref"]
    assert "http_status" not in body
    assert "commit" in events
    assert "respond:200" in events
    assert events.index("commit") < events.index("respond:200")
    assert engine.held_amount(DEMO_ACCOUNT_ID) > 0
    refs = [entry.ref for entry in engine.entries(DEMO_ACCOUNT_ID) if entry.kind == "task_hold"]
    assert REQUEST in refs
    assert expected_event_id("task_hold", REQUEST) not in refs


def test_settle_without_hold_maps_409_predecessor_pending():
    _engine, adapter = _runtime()
    client = TestClient(create_app(adapter=adapter, settings=_settings()))
    settle = {
        "schema_version": "0",
        "event_id": expected_event_id("task_settlement", REQUEST),
        "kind": "task_settlement",
        "occurred_at": "2026-11-09T13:01:00Z",
        "request_id": REQUEST,
        "account_id": DEMO_ACCOUNT_ID,
        "billing": "platform",
        "meter": {
            "model_id": "demo-text-v0",
            "modality": "text",
            "unit_kind": "tokens",
            "input_units": "80",
            "output_units": "16",
            "estimated": False,
        },
    }
    response = client.post(METER_EVENTS_PATH, json=settle, headers=AUTH_HEADERS)
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "predecessor_pending"
    assert body["retryable"] is True


def test_malformed_json_is_400():
    _engine, adapter = _runtime()
    client = TestClient(create_app(adapter=adapter, settings=_settings()))
    response = client.post(
        METER_EVENTS_PATH,
        content="{not-json",
        headers={**AUTH_HEADERS, "content-type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json() == {"code": "invalid_payload"}


def test_from_env_requires_dedicated_callback_vars(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    monkeypatch.delenv(CREDENTIAL_ENV, raising=False)
    with pytest.raises(MeterHttpConfigError, match=TOKEN_ENV):
        MeterHttpSettings.from_env({})
    with pytest.raises(MeterHttpConfigError, match=CREDENTIAL_ENV):
        MeterHttpSettings.from_env({TOKEN_ENV: LOCAL_DEV_TOKEN})
    settings = MeterHttpSettings.from_env(
        {
            TOKEN_ENV: LOCAL_DEV_TOKEN,
            CREDENTIAL_ENV: LOCAL_DEV_CREDENTIAL_ID,
        }
    )
    assert settings.token == LOCAL_DEV_TOKEN
    assert settings.credential_id == LOCAL_DEV_CREDENTIAL_ID
    assert settings.host == "127.0.0.1"
    assert settings.port == 8090


def test_from_env_rejects_customer_msk_prefix():
    with pytest.raises(MeterHttpConfigError, match="msk_"):
        MeterHttpSettings.from_env(
            {
                TOKEN_ENV: "msk_not_a_real_customer_key",
                CREDENTIAL_ENV: LOCAL_DEV_CREDENTIAL_ID,
            }
        )


def test_from_env_does_not_read_kit_meter_api_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KIT_METER_API_TOKEN", "bootstrap-must-not-be-used")
    monkeypatch.setenv(TOKEN_ENV, LOCAL_DEV_TOKEN)
    monkeypatch.setenv(CREDENTIAL_ENV, LOCAL_DEV_CREDENTIAL_ID)
    settings = MeterHttpSettings.from_env()
    assert settings.token == LOCAL_DEV_TOKEN
    assert settings.token != "bootstrap-must-not-be-used"


def test_callback_source_never_falls_back_to_kit_meter_token():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    assert 'os.environ.get("KIT_METER_API_TOKEN")' not in text
    assert 'os.environ["KIT_METER_API_TOKEN"]' not in text
    assert TOKEN_ENV in text
    assert CREDENTIAL_ENV in text
    imports = [
        line.strip() for line in text.splitlines() if line.lstrip().startswith(("import ", "from "))
    ]
    joined = "\n".join(imports).lower()
    assert "kit_meter" not in joined
    assert "kit-meter" not in joined
    assert "kit_model_router" not in joined
