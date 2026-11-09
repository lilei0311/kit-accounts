"""Local/staging inbound for ``POST /internal/v0/meter-events``.

Thin ASGI shell around ``MeterEventAdapter``. Not a production gateway,
not an outbox, and not a Hub push. Callback credentials come from
``POINTS_METER_CALLBACK_TOKEN`` / ``POINTS_METER_CALLBACK_CREDENTIAL_ID``.
Customer ``msk_…`` keys and ``KIT_METER_API_TOKEN`` are not used.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from maxkit_points import MemoryPointsStore, PointsEngine
from maxkit_usage_bridge import MeterCallbackAck, MeterEventAdapter, PointsUsageSink, RateTable

METER_EVENTS_PATH = "/internal/v0/meter-events"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8090
DEMO_ACCOUNT_ID = "acct_demo"
DEMO_GRANT_AMOUNT = 1_000_000
DEMO_GRANT_KEY = "local-http-demo-grant"
TOKEN_ENV = "POINTS_METER_CALLBACK_TOKEN"
CREDENTIAL_ENV = "POINTS_METER_CALLBACK_CREDENTIAL_ID"
HOST_ENV = "POINTS_METER_HTTP_HOST"
PORT_ENV = "POINTS_METER_HTTP_PORT"
# Documented 联调-only placeholders. Not production secrets.
LOCAL_DEV_TOKEN = "local-only-meter-callback"
LOCAL_DEV_CREDENTIAL_ID = "cred_local_points_meter"


class MeterHttpConfigError(RuntimeError):
    """Missing or forbidden callback-credential configuration."""


@dataclass(frozen=True)
class MeterHttpSettings:
    token: str
    credential_id: str
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> MeterHttpSettings:
        env = os.environ if environ is None else environ
        token = (env.get(TOKEN_ENV) or "").strip()
        credential_id = (env.get(CREDENTIAL_ENV) or "").strip()
        if not token:
            raise MeterHttpConfigError(f"{TOKEN_ENV} is required (联调 service credential)")
        if not credential_id:
            raise MeterHttpConfigError(f"{CREDENTIAL_ENV} is required")
        _reject_customer_msk(token, field=TOKEN_ENV)
        _reject_customer_msk(credential_id, field=CREDENTIAL_ENV)
        host = (env.get(HOST_ENV) or DEFAULT_HOST).strip() or DEFAULT_HOST
        port_raw = (env.get(PORT_ENV) or "").strip()
        port = DEFAULT_PORT if not port_raw else _parse_port(port_raw)
        return cls(token=token, credential_id=credential_id, host=host, port=port)


def _reject_customer_msk(value: str, *, field: str) -> None:
    if value.startswith("msk_"):
        raise MeterHttpConfigError(
            f"{field} must not be a customer msk_… key; use the dedicated callback credential"
        )


def _parse_port(raw: str) -> int:
    try:
        port = int(raw)
    except ValueError as exc:
        raise MeterHttpConfigError(f"{PORT_ENV} must be an integer") from exc
    if port < 1 or port > 65535:
        raise MeterHttpConfigError(f"{PORT_ENV} must be in 1..65535")
    return port


def _bearer_token(header: str | None) -> str | None:
    if header is None:
        return None
    scheme, _, rest = header.partition(" ")
    if scheme.casefold() != "bearer" or not rest.strip():
        return None
    return rest.strip()


def authorize(request: Request, settings: MeterHttpSettings) -> JSONResponse | None:
    provided = _bearer_token(request.headers.get("authorization"))
    if provided is None or not hmac.compare_digest(provided, settings.token):
        return JSONResponse({"code": "service_auth_invalid"}, status_code=401)
    cred = (request.headers.get("credential_id") or "").strip()
    if not cred or not hmac.compare_digest(cred, settings.credential_id):
        return JSONResponse({"code": "service_forbidden"}, status_code=403)
    return None


def ack_body(ack: MeterCallbackAck) -> dict[str, object]:
    """HTTP JSON: contract echo fields. Status stays on the transport line."""

    body: dict[str, object] = {
        "schema_version": ack.schema_version,
        "event_id": ack.event_id,
        "request_id": ack.request_id,
        "kind": ack.kind,
    }
    if ack.result is not None:
        body["result"] = ack.result
    if ack.code is not None:
        body["code"] = ack.code
    if ack.original_result is not None:
        body["original_result"] = ack.original_result
    if ack.snapshot_ref is not None:
        body["snapshot_ref"] = ack.snapshot_ref
    if ack.effect is not None:
        body["effect"] = ack.effect
    if ack.retryable:
        body["retryable"] = True
    return body


def create_app(
    *,
    adapter: MeterEventAdapter,
    settings: MeterHttpSettings,
) -> Starlette:
    async def meter_events(request: Request) -> JSONResponse:
        denied = authorize(request, settings)
        if denied is not None:
            return denied
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"code": "invalid_payload"}, status_code=400)
        except Exception:
            return JSONResponse({"code": "invalid_payload"}, status_code=400)
        ack = adapter.handle(payload)
        # handle() returns only after the points transaction has committed
        # (or after validation, which does not persist a business result).
        return JSONResponse(ack_body(ack), status_code=ack.http_status)

    return Starlette(
        routes=[Route(METER_EVENTS_PATH, meter_events, methods=["POST"])],
    )


def build_local_runtime() -> tuple[PointsEngine, MeterEventAdapter]:
    """In-memory ledger + snapshot rates, seeded so a documented curl hold applies."""

    engine = PointsEngine(store=MemoryPointsStore())
    engine.apply_grant(
        owner_id=DEMO_ACCOUNT_ID,
        amount=DEMO_GRANT_AMOUNT,
        key=DEMO_GRANT_KEY,
    )
    sink = PointsUsageSink(engine, RateTable.from_snapshot())
    return engine, MeterEventAdapter(sink)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="maxkit-meter-http",
        description=(
            "Local/staging meter-events inbound. "
            f"Default listen URL is http://{DEFAULT_HOST}:{DEFAULT_PORT}."
        ),
    )
    parser.parse_args(argv)
    try:
        settings = MeterHttpSettings.from_env()
    except MeterHttpConfigError as exc:
        print(f"maxkit-meter-http: {exc}", file=sys.stderr)
        return 2
    _engine, adapter = build_local_runtime()
    app = create_app(adapter=adapter, settings=settings)
    print(
        f"listening on http://{settings.host}:{settings.port}{METER_EVENTS_PATH}",
        file=sys.stderr,
    )
    uvicorn.run(app, host=settings.host, port=settings.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
