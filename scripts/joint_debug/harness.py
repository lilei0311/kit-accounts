"""Pure helpers for the dual-ledger joint-debug runner. Not a kit package."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

# kit-meter PR #9 lock (short). TechBot should set KIT_METER_PIN to the full SHA.
DEFAULT_KIT_METER_PIN = "40967aae"
MIN_PIN_LEN = 7
STRIP_ENV = (
    "VIRTUAL_ENV",
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "UV_PROJECT_ENVIRONMENT",
)


class PinMismatchError(RuntimeError):
    """ROOT HEAD does not match the explicit pin; refusing a silent checkout."""


def expected_pin(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    pin = (env.get("KIT_METER_PIN") or DEFAULT_KIT_METER_PIN).strip()
    if len(pin) < MIN_PIN_LEN:
        raise ValueError(
            f"KIT_METER_PIN too short ({pin!r}); need at least {MIN_PIN_LEN} hex chars"
        )
    return pin


def checkout_explicit(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return (env.get("KIT_METER_CHECKOUT_PIN") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def sha_matches_pin(full_sha: str, pin: str) -> bool:
    sha = (full_sha or "").strip().lower()
    needle = (pin or "").strip().lower()
    if len(sha) < MIN_PIN_LEN or len(needle) < MIN_PIN_LEN:
        return False
    return sha.startswith(needle) or needle.startswith(sha)


def git_full_sha(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    sha = (proc.stdout or "").strip()
    if proc.returncode != 0 or len(sha) < MIN_PIN_LEN:
        err = (proc.stderr or proc.stdout or "rev-parse failed").strip()
        raise RuntimeError(f"cannot read HEAD SHA in {repo}: {err}")
    return sha


def jsonable(value: Any) -> Any:
    """JSON-safe dump. Postgres Numeric stays a decimal string (never float)."""

    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


def dumps_json(payload: Any) -> str:
    return json.dumps(jsonable(payload), ensure_ascii=False, indent=2) + "\n"


def isolated_child_env(
    extra: Mapping[str, str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Drop parent venv / PYTHONPATH so Hub and points each use their own .venv."""

    env = dict(os.environ if environ is None else environ)
    home_bin = str(Path.home() / ".local" / "bin")
    path = env.get("PATH", "")
    if home_bin not in path.split(":"):
        env["PATH"] = f"{home_bin}:{path}" if path else home_bin
    if extra:
        env.update(extra)
    # Strip after extra so a parent VIRTUAL_ENV/PYTHONPATH cannot leak back in.
    for key in STRIP_ENV:
        env.pop(key, None)
    return env


def psycopg_is_in_project_venv(psycopg_file: str, sys_prefix: str, repo: Path) -> tuple[bool, str]:
    venv = (repo / ".venv").resolve()
    file_path = Path(psycopg_file).resolve()
    prefix = Path(sys_prefix).resolve()
    try:
        file_ok = file_path.is_relative_to(venv)
    except ValueError:
        file_ok = False
    try:
        prefix_ok = prefix == venv or prefix.is_relative_to(venv)
    except ValueError:
        prefix_ok = False
    ok = file_ok or prefix_ok
    detail = f"psycopg={file_path} prefix={prefix} venv={venv}"
    return ok, detail


def inbound_quantity_strings(estimate: Mapping[str, Any]) -> dict[str, Any]:
    """Hub estimate may use ints; inbound contract wants decimal strings."""

    body = dict(estimate)
    for key in ("input_units", "output_units", "units"):
        if key not in body or body[key] is None:
            continue
        raw = body[key]
        if isinstance(raw, str):
            body[key] = raw
        elif isinstance(raw, bool) or isinstance(raw, float):
            raise TypeError(f"{key} must not be float/bool; got {raw!r}")
        else:
            body[key] = format(Decimal(str(raw)), "f")
    return body


def late_hold_envelope(
    request_id: str,
    account_id: str,
    estimate: Mapping[str, Any],
    *,
    occurred_at: str = "2026-11-09T13:00:00Z",
) -> dict[str, Any]:
    qty = inbound_quantity_strings(estimate)
    return {
        "schema_version": "0",
        "event_id": f"evt_hold_{request_id}",
        "kind": "task_hold",
        "occurred_at": occurred_at,
        "request_id": request_id,
        "account_id": account_id,
        "billing": "platform",
        "estimate": qty,
    }


def outbox_row_acked(row: Mapping[str, Any]) -> bool:
    status = str(row.get("delivery_status") or row.get("status") or "").strip().lower()
    return status in {"acked", "ack", "delivered", "done", "success"}


def require_meter_head_matches(
    full_sha: str,
    pin: str,
    *,
    root: Path,
    allow_checkout: bool,
) -> None:
    if sha_matches_pin(full_sha, pin):
        return
    if allow_checkout:
        return
    raise PinMismatchError(
        f"KIT_METER_REPO_ROOT={root} HEAD {full_sha} does not match "
        f"KIT_METER_PIN={pin}. Refusing to checkout (would roll back meter "
        "fixes). Set KIT_METER_PIN to this HEAD, or KIT_METER_CHECKOUT_PIN=1 "
        "to move HEAD explicitly."
    )
