"""Joint-debug points inbound HTTP.

Wraps ``maxkit_internal_http.create_app`` with a local Postgres (preferred)
or Memory ledger and seeds A–F accounts. Does not change kit packages.
Captures every ``POST /internal/v0/meter-events`` body to JSONL for 对账.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from maxkit_internal_http import MeterHttpSettings, create_app
from maxkit_internal_http.http import DEMO_ACCOUNT_ID, DEMO_GRANT_AMOUNT, DEMO_GRANT_KEY
from maxkit_points import MemoryPointsStore, PointsEngine
from maxkit_usage_bridge import MeterEventAdapter, PointsUsageSink, RateTable

JOINT_ACCOUNT = "acct_joint_debug"
POOR_ACCOUNT = "acct_joint_debug_poor"
SHORT_ACCOUNT = "acct_joint_debug_short"
JOINT_GRANT = 1_000_000
POOR_GRANT = 1
SHORT_GRANT = 5

CAPTURE_ENV = "JOINT_DEBUG_CAPTURE_PATH"
DSN_ENV = "MAXKIT_POINTS_DSN"
SCHEMA_ENV = "MAXKIT_POINTS_SCHEMA"


class InboundCaptureMiddleware(BaseHTTPMiddleware):
    """Append request/response JSON for the meter-events path. Harness only."""

    def __init__(self, app: Any, path: Path) -> None:
        super().__init__(app)
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        raw = await request.body()
        response = await call_next(request)
        chunks = [chunk async for chunk in response.body_iterator]
        body = b"".join(chunks)
        record = {
            "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "request": _decode_json(raw),
            "response": _decode_json(body),
            "authorization_present": bool(request.headers.get("authorization")),
            "credential_id": request.headers.get("credential_id"),
        }
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        headers = dict(response.headers)
        headers.pop("content-length", None)
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )


def _decode_json(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"_raw": raw.decode("utf-8", errors="replace")}


def build_joint_runtime(
    dsn: str | None = None, schema: str = "points"
) -> tuple[PointsEngine, MeterEventAdapter, str]:
    """Return engine+adapter and a backend label (postgres|memory)."""

    store: Any
    backend = "memory"
    if dsn:
        from maxkit_points.postgres import PostgresPointsStore

        store = PostgresPointsStore(dsn, schema=schema)
        backend = "postgres"
    else:
        store = MemoryPointsStore()
    engine = PointsEngine(store=store)
    _seed_accounts(engine)
    sink = PointsUsageSink(engine, RateTable.from_snapshot())
    return engine, MeterEventAdapter(sink), backend


def _seed_accounts(engine: PointsEngine) -> None:
    grants = (
        (DEMO_ACCOUNT_ID, DEMO_GRANT_AMOUNT, DEMO_GRANT_KEY),
        (JOINT_ACCOUNT, JOINT_GRANT, "joint-debug-grant"),
        (POOR_ACCOUNT, POOR_GRANT, "joint-debug-poor-grant"),
        (SHORT_ACCOUNT, SHORT_GRANT, "joint-debug-short-grant"),
    )
    for owner, amount, key in grants:
        engine.apply_grant(owner_id=owner, amount=amount, key=key)


def main(_argv: list[str] | None = None) -> int:
    try:
        settings = MeterHttpSettings.from_env()
    except Exception as exc:  # MeterHttpConfigError
        print(f"start_points_http: {exc}", file=sys.stderr)
        return 2
    dsn = (os.environ.get(DSN_ENV) or "").strip() or None
    schema = (os.environ.get(SCHEMA_ENV) or "points").strip() or "points"
    try:
        _engine, adapter, backend = build_joint_runtime(dsn, schema)
    except Exception as exc:
        print(
            f"start_points_http: postgres failed ({exc}); falling back to Memory", file=sys.stderr
        )
        _engine, adapter, backend = build_joint_runtime(None)
    app = create_app(adapter=adapter, settings=settings)
    capture = (os.environ.get(CAPTURE_ENV) or "").strip()
    if capture:
        app.add_middleware(InboundCaptureMiddleware, path=Path(capture))
    print(
        f"listening on http://{settings.host}:{settings.port}/internal/v0/meter-events "
        f"backend={backend}",
        file=sys.stderr,
    )
    uvicorn.run(app, host=settings.host, port=settings.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
