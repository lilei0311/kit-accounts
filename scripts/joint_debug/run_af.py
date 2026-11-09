"""Dual-ledger A–F: Hub business API → outbox pump → points HTTP.

Local / staging only. Does not import kit-meter, does not write production,
does not mint real secrets. Prefer Postgres on both sides; Memory on points
only if local PG cannot be used.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from harness import (  # noqa: E402
    DEFAULT_KIT_METER_PIN,
    PinMismatchError,
    checkout_explicit,
    dumps_json,
    expected_pin,
    git_full_sha,
    isolated_child_env,
    jsonable,
    late_hold_envelope,
    outbox_row_acked,
    psycopg_is_in_project_venv,
    require_meter_head_matches,
    sha_matches_pin,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = REPO_ROOT / "docs" / "joint-debug-af" / "evidence"
REPORT_MD = REPO_ROOT / "docs" / "joint-debug-af-evidence.md"
REPORT_JSON = REPO_ROOT / "docs" / "joint-debug-af" / "run.json"

KIT_METER_URL = "https://github.com/lilei0311/kit-meter.git"
KIT_METER_DEFAULT = Path("/tmp/dual-ledger/kit-meter")

POINTS_HOST = "127.0.0.1"
POINTS_PORT = 8090
HUB_HOST = "127.0.0.1"
HUB_PORT = 8088
CALLBACK_TOKEN = "local-only-meter-callback"
CALLBACK_CREDENTIAL = "cred_local_points_meter"
BOOTSTRAP_TOKEN = "joint-debug-bootstrap-only"

JOINT_ACCOUNT = "acct_joint_debug"
POOR_ACCOUNT = "acct_joint_debug_poor"
SHORT_ACCOUNT = "acct_joint_debug_short"

ADMIN_DSN = "postgresql:///postgres?host=/var/run/postgresql"
POINTS_DB = "kit_accounts_joint"
POINTS_OWNER = "kit_accounts_joint_owner"
POINTS_RUNTIME = "kit_accounts_joint_rt"
POINTS_RUNTIME_PW = "kit_accounts_joint_rt"
METER_DB = "kit_meter_joint"
METER_USER = "kitmeter"

ESTIMATE_A = {
    "model_id": "demo-text-v0",
    "modality": "text",
    "unit_kind": "tokens",
    "input_units": 100,
    "output_units": 20,
}
USAGE_A = {
    "model_id": "demo-text-v0",
    "modality": "text",
    "unit_kind": "tokens",
    "input_units": 80,
    "output_units": 16,
}
ESTIMATE_C = {
    "model_id": "demo-text-v0",
    "modality": "text",
    "unit_kind": "tokens",
    "input_units": 0,
    "output_units": 100000,
}
ESTIMATE_F = {
    "model_id": "demo-text-v0",
    "modality": "text",
    "unit_kind": "tokens",
    "input_units": 0,
    "output_units": 5000,
}
USAGE_F = {
    "model_id": "demo-text-v0",
    "modality": "text",
    "unit_kind": "tokens",
    "input_units": 0,
    "output_units": 10000,
}


@dataclass
class HttpCall:
    method: str
    url: str
    request: Any
    status: int | None
    body: Any
    text: str
    error: str | None = None


@dataclass
class HubIssue:
    seq: str
    title: str
    detail: str
    request: Any = None
    response: Any = None


@dataclass
class CaseResult:
    seq: str
    title: str
    verdict: str
    attribution: str
    summary: str
    request_id: str = ""
    hub_task: Any = None
    hub_outbox: Any = None
    points_acks: list[Any] = field(default_factory=list)
    points_billing: Any = None
    points_entries: list[Any] = field(default_factory=list)
    points_balance: Any = None
    hub_http: list[Any] = field(default_factory=list)
    hub_issues: list[Any] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    mode: str = "hub"  # "hub" or "points-inbound-sim"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _jsonable(value: Any) -> Any:
    return jsonable(value)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_json(payload), encoding="utf-8")


def _decode_body(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except json.JSONDecodeError:
        return None


class HubClient:
    def __init__(self, base: str, token: str | None = None) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.http = httpx.Client(timeout=15.0)
        self.calls: list[HttpCall] = []

    def close(self) -> None:
        self.http.close()

    def request(self, method: str, path: str, payload: Any | None = None) -> HttpCall:
        url = f"{self.base}{path}"
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body: Any = {} if payload is None else payload
        kwargs: dict[str, Any] = {"headers": headers}
        if method.upper() != "GET":
            kwargs["json"] = body
        try:
            resp = self.http.request(method, url, **kwargs)
            call = HttpCall(
                method=method,
                url=url,
                request=body,
                status=resp.status_code,
                body=_decode_body(resp),
                text=resp.text,
            )
        except httpx.HTTPError as exc:
            call = HttpCall(
                method=method,
                url=url,
                request=body,
                status=None,
                body=None,
                text="",
                error=str(exc),
            )
        self.calls.append(call)
        return call


def wait_http(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            httpx.get(url, timeout=2.0)
            return True
        except httpx.HTTPStatusError:
            return True
        except httpx.HTTPError as exc:
            last = str(exc)
            time.sleep(0.3)
    print(f"wait_http timeout {url}: {last}", file=sys.stderr)
    return False


def wait_post(url: str, headers: dict[str, str], timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            httpx.post(url, json={"probe": True}, headers=headers, timeout=2.0)
            return True
        except httpx.HTTPError:
            time.sleep(0.3)
    return False


def pg_admin() -> psycopg.Connection:
    return psycopg.connect(ADMIN_DSN, autocommit=True)


def recreate_db(name: str, owner: str) -> None:
    with pg_admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid()
                """,
                (name,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
            cur.execute(f'CREATE DATABASE "{name}" OWNER "{owner}"')


def ensure_role(name: str, *, login: bool, password: str | None = None) -> None:
    ident = sql.Identifier(name)
    with pg_admin() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
            exists = cur.fetchone() is not None
            if not exists and login:
                cur.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        ident, sql.Literal(password or name)
                    )
                )
            elif not exists:
                cur.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(ident))
            elif login and password is not None:
                cur.execute(
                    sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                        ident, sql.Literal(password)
                    )
                )
            elif login:
                cur.execute(sql.SQL("ALTER ROLE {} WITH LOGIN").format(ident))


def setup_points_postgres() -> str:
    """Throwaway local DB + schema points. Returns runtime DSN."""

    ensure_role(POINTS_OWNER, login=False)
    ensure_role(POINTS_RUNTIME, login=True, password=POINTS_RUNTIME_PW)
    recreate_db(POINTS_DB, "ubuntu")
    db_admin = f"postgresql:///{POINTS_DB}?host=/var/run/postgresql"
    sql_path = (
        REPO_ROOT / "packages" / "points" / "src" / "maxkit_points" / "migrations" / "001_init.sql"
    )
    sql = sql_path.read_text(encoding="utf-8").replace("__RUNTIME_ROLE__", POINTS_RUNTIME)
    with psycopg.connect(db_admin, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(f"CREATE SCHEMA points AUTHORIZATION {POINTS_OWNER}")
            cur.execute(f'GRANT CONNECT ON DATABASE "{POINTS_DB}" TO {POINTS_RUNTIME}')
            cur.execute(f"GRANT USAGE ON SCHEMA points TO {POINTS_RUNTIME}")
            cur.execute(f"SET ROLE {POINTS_OWNER}")
            cur.execute("SET search_path TO points")
            cur.execute(sql)
            cur.execute("RESET ROLE")
        conn.commit()
    return (
        f"postgresql://{POINTS_RUNTIME}:{POINTS_RUNTIME_PW}"
        f"@127.0.0.1:5432/{POINTS_DB}?options=-csearch_path%3Dpoints"
    )


def setup_meter_postgres(repo: Path) -> str:
    meter_pw = "kitmeter"
    ensure_role(METER_USER, login=True, password=meter_pw)
    recreate_db(METER_DB, METER_USER)
    dsn = f"postgresql://{METER_USER}:{meter_pw}@127.0.0.1:5432/{METER_DB}"
    sql_dir = repo / "packages" / "meter" / "sql"
    files = ["001_meter_and_cost.sql", "002_meter_api_keys.sql", "003_points_outbox.sql"]
    applied = []
    for name in files:
        path = sql_dir / name
        if not path.exists():
            if name == "002_meter_api_keys.sql":
                continue
            raise FileNotFoundError(path)
        subprocess.run(
            ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-f", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
        applied.append(name)
    print(f"meter sql applied: {applied}", file=sys.stderr)
    return dsn


def _looks_like_meter(path: Path) -> bool:
    return (path / "pyproject.toml").exists() or (path / "docs" / "http-api.md").exists()


def _checkout_pin(repo: Path, pin: str) -> None:
    if not (repo / ".git").exists():
        raise RuntimeError(f"{repo} is not a git tree; cannot checkout {pin}")
    fetch = subprocess.run(
        ["git", "-C", str(repo), "fetch", "--depth", "1", "origin", pin],
        capture_output=True,
        text=True,
        check=False,
    )
    proc = subprocess.run(
        ["git", "-C", str(repo), "checkout", "--detach", pin],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"explicit checkout of {pin} failed in {repo}: "
            f"{(proc.stderr or fetch.stderr or proc.stdout).strip()}"
        )


def locate_or_clone_kit_meter() -> tuple[Path | None, str, str]:
    """Return (path, note, full_sha). Never silently roll ROOT back to an old pin."""

    pin = expected_pin()
    root_raw = os.environ.get("KIT_METER_REPO_ROOT", "").strip()
    allow = checkout_explicit()
    if root_raw:
        path = Path(root_raw).expanduser().resolve()
        if not _looks_like_meter(path):
            return None, f"KIT_METER_REPO_ROOT={path} is not a kit-meter tree", ""
        sha = git_full_sha(path)
        try:
            require_meter_head_matches(sha, pin, root=path, allow_checkout=allow)
        except PinMismatchError as exc:
            return None, str(exc), sha
        if not sha_matches_pin(sha, pin):
            _checkout_pin(path, pin)
            sha = git_full_sha(path)
            if not sha_matches_pin(sha, pin):
                return None, f"after checkout HEAD {sha} still != pin {pin}", sha
        return path, f"ROOT {path} HEAD {sha} matches pin {pin}", sha

    dest = KIT_METER_DEFAULT
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and _looks_like_meter(dest) and (dest / ".git").exists():
        sha = git_full_sha(dest)
        if sha_matches_pin(sha, pin):
            return dest, f"reused {dest} HEAD {sha} matches pin {pin}", sha
        return (
            None,
            f"found {dest} HEAD {sha} != pin {pin}; set KIT_METER_REPO_ROOT "
            f"and KIT_METER_CHECKOUT_PIN=1 to move it, or point ROOT at {pin}",
            sha,
        )
    if dest.exists():
        shutil.rmtree(dest)
    proc = subprocess.run(
        ["git", "clone", KIT_METER_URL, str(dest)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return None, f"git clone failed: {err}", ""
    _checkout_pin(dest, pin)
    sha = git_full_sha(dest)
    if not sha_matches_pin(sha, pin):
        return None, f"cloned HEAD {sha} does not match pin {pin}", sha
    return dest, f"cloned {dest} HEAD {sha} (pin {pin})", sha


def start_tmux(name: str, cwd: Path, command: str, extra: dict[str, str]) -> None:
    subprocess.run(["tmux", "kill-session", "-t", name], check=False, capture_output=True)
    wrapper_dir = Path("/tmp/joint-debug")
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    wrapper = wrapper_dir / f"start-{name}.sh"
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "unset VIRTUAL_ENV PYTHONPATH PYTHONHOME PYTHONSTARTUP UV_PROJECT_ENVIRONMENT",
        f"cd {_shell_quote(str(cwd))}",
    ]
    for key, value in isolated_child_env(extra).items():
        if key in extra or key == "PATH":
            lines.append(f"export {key}={_shell_quote(value)}")
    lines.append(command)
    lines.append("echo EXIT:$?")
    wrapper.write_text("\n".join(lines) + "\n", encoding="utf-8")
    wrapper.chmod(0o755)
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", name, "-c", str(cwd), "bash", str(wrapper)],
        check=True,
        env=isolated_child_env(),
    )


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def tmux_capture(name: str) -> str:
    proc = subprocess.run(
        ["tmux", "capture-pane", "-pt", f"{name}:0.0", "-S", "-80"],
        capture_output=True,
        text=True,
    )
    return proc.stdout


def stop_tmux(*names: str) -> None:
    for name in names:
        subprocess.run(["tmux", "kill-session", "-t", name], check=False, capture_output=True)


def hub_sync(repo: Path) -> str:
    """Install Hub deps into *that* project's venv. Fail loudly. No sitecustomize."""

    env = isolated_child_env()
    cmd = ["uv", "sync", "--extra", "http"]
    if (repo / "uv.lock").exists():
        cmd = ["uv", "sync", "--locked", "--extra", "http"]
    synced = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True)
    if synced.returncode != 0:
        raise RuntimeError(
            f"uv sync failed in {repo} ({' '.join(cmd)}):\n{synced.stderr or synced.stdout}"
        )
    pip = subprocess.run(
        ["uv", "pip", "install", "psycopg[binary]>=3.1"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    if pip.returncode != 0:
        raise RuntimeError(
            f"uv pip install psycopg failed in Hub venv {repo}:\n{pip.stderr or pip.stdout}"
        )
    probe = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "-c",
            "import psycopg,sys; print(psycopg.__file__); print(sys.prefix)",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        raise RuntimeError(f"Hub venv cannot import psycopg:\n{probe.stderr or probe.stdout}")
    lines = [ln.strip() for ln in probe.stdout.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise RuntimeError(f"unexpected psycopg probe output: {probe.stdout!r}")
    ok, detail = psycopg_is_in_project_venv(lines[0], lines[1], repo)
    if not ok:
        raise RuntimeError(
            "psycopg is not installed in kit-meter's own .venv "
            f"(clean run must not use parent venv / sitecustomize): {detail}"
        )
    return detail


def mint_key(hub: HubClient) -> str:
    call = hub.request(
        "POST",
        "/v1alpha/keys",
        {
            "account_id": JOINT_ACCOUNT,
            "name": "joint-debug",
            "scopes": ["hub.meter.read", "hub.meter.write", "hub.meter.ops"],
        },
    )
    body = call.body if isinstance(call.body, dict) else {}
    token = str(body.get("token") or body.get("key") or "")
    if not token.startswith("msk_"):
        raise RuntimeError(
            f"key mint did not return msk_ token: status={call.status} body={call.text}"
        )
    return token


def query_points(dsn: str, request_id: str, account_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "billing": None,
        "entries": [],
        "available": None,
        "held": None,
    }
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT request_id, account_id, status, snapshot_ref, points, "
                "shortfall_points, reject_reason, available_after, effect, "
                "skipped_reason, billing, model_id, unit_kind "
                "FROM billing_requests WHERE request_id = %s",
                (request_id,),
            )
            row = cur.fetchone()
            out["billing"] = dict(row) if row else None
            cur.execute(
                "SELECT id, owner_id, kind, delta, key, ref, at "
                "FROM entries WHERE ref = %s OR key = %s OR key LIKE %s "
                "ORDER BY at, id",
                (request_id, request_id, f"%{request_id}%"),
            )
            out["entries"] = [dict(r) for r in cur.fetchall()]
            cur.execute(
                "SELECT COALESCE(SUM(opened),0) AS available FROM point_lots "
                "WHERE owner_id = %s AND opened > 0 "
                "AND (expires_at IS NULL OR expires_at > now())",
                (account_id,),
            )
            out["available"] = int(cur.fetchone()["available"])
            cur.execute(
                "SELECT COALESCE(SUM(amount),0) AS held FROM holds "
                "WHERE owner_id = %s AND status = 'held'",
                (account_id,),
            )
            out["held"] = int(cur.fetchone()["held"])
    return out


def query_meter(dsn: str, request_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {"task": None, "outbox": [], "schema": {}}
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        schemas = []
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_name IN ('tasks', 'points_outbox', 'outbox')
                   OR table_name LIKE '%outbox%'
                   OR table_name LIKE '%task%'
                ORDER BY table_schema, table_name
                """
            )
            schemas = [dict(r) for r in cur.fetchall()]
        out["schema"] = schemas
        task_rel = None
        for row in schemas:
            if row["table_name"] == "tasks":
                task_rel = (row["table_schema"], row["table_name"])
                break
        outbox_rel = None
        for row in schemas:
            if row["table_name"] in {"points_outbox", "outbox"}:
                outbox_rel = (row["table_schema"], row["table_name"])
                break
            if "outbox" in row["table_name"]:
                outbox_rel = (row["table_schema"], row["table_name"])
        if task_rel:
            schema, table = task_rel
            with conn.cursor() as cur:
                cur.execute(
                    f'SELECT * FROM "{schema}"."{table}" WHERE request_id = %s',
                    (request_id,),
                )
                row = cur.fetchone()
                out["task"] = dict(row) if row else None
        if outbox_rel:
            schema, table = outbox_rel
            with conn.cursor() as cur:
                cur.execute(
                    f'SELECT * FROM "{schema}"."{table}" WHERE request_id = %s',
                    (request_id,),
                )
                out["outbox"] = [dict(r) for r in cur.fetchall()]
    return out


def reset_outbox_pending(dsn: str, request_id: str, event_id: str) -> dict[str, Any]:
    """Ops replay: same event_id back to pending. Not a Hub API."""

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_name IN ('points_outbox', 'outbox')
                   OR table_name LIKE '%outbox%'
                """
            )
            rels = cur.fetchall()
            if not rels:
                return {"updated": 0, "error": "no outbox table"}
            schema, table = rels[0]["table_schema"], rels[0]["table_name"]
            cur.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                """,
                (schema, table),
            )
            cols = {r["column_name"] for r in cur.fetchall()}
            status_col = None
            for candidate in ("delivery_status", "status", "state"):
                if candidate in cols:
                    status_col = candidate
                    break
            if status_col is None:
                return {"updated": 0, "error": f"no status column in {schema}.{table}"}
            cur.execute(
                f"""
                UPDATE "{schema}"."{table}"
                SET {status_col} = 'pending'
                WHERE request_id = %s AND event_id = %s
                RETURNING event_id, {status_col} AS delivery_status
                """,
                (request_id, event_id),
            )
            rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
    return {"updated": len(rows), "rows": rows, "table": f"{schema}.{table}"}


def load_capture(path: Path, request_id: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        req = rec.get("request") or {}
        resp = rec.get("response") or {}
        if req.get("request_id") == request_id or resp.get("request_id") == request_id:
            rows.append(rec)
    return rows


def ack_results(acks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for rec in acks:
        resp = rec.get("response") or {}
        req = rec.get("request") or {}
        out.append(
            {
                "status": rec.get("status"),
                "kind": req.get("kind") or resp.get("kind"),
                "event_id": req.get("event_id") or resp.get("event_id"),
                "result": resp.get("result"),
                "original_result": resp.get("original_result"),
                "snapshot_ref": resp.get("snapshot_ref"),
                "code": resp.get("code"),
                "effect": resp.get("effect"),
                "request": req,
                "response": resp,
            }
        )
    return out


def find_rid(call: HttpCall) -> str:
    body = call.body if isinstance(call.body, dict) else {}
    return str(body.get("request_id") or body.get("id") or "")


def iso_plus(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


class Runner:
    def __init__(self) -> None:
        self.evidence = EVIDENCE_DIR
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.capture_path = self.evidence / "points-inbound.jsonl"
        self.points_dsn: str | None = None
        self.meter_dsn: str | None = None
        self.meter_repo: Path | None = None
        self.meter_sha = ""
        self.meter_pin = expected_pin()
        self.points_backend = "unknown"
        self.clone_note = ""
        self.hub_issues: list[HubIssue] = []
        self.cases: list[CaseResult] = []
        self.hub: HubClient | None = None
        self.env_notes: list[str] = []
        self.e1_rid = ""
        self.e1_account = JOINT_ACCOUNT
        self.e1_estimate = ESTIMATE_A

    def run(self) -> int:
        print(f"evidence -> {self.evidence}", file=sys.stderr)
        try:
            self.points_dsn = setup_points_postgres()
            self.points_backend = "postgres"
            self.env_notes.append(
                "points ledger: local Postgres kit_accounts_joint (schema points)"
            )
        except Exception as exc:
            self.points_dsn = None
            self.points_backend = "memory"
            self.env_notes.append(f"points Postgres unavailable ({exc}); Memory fallback")
            print(self.env_notes[-1], file=sys.stderr)

        points_env = {
            "POINTS_METER_CALLBACK_TOKEN": CALLBACK_TOKEN,
            "POINTS_METER_CALLBACK_CREDENTIAL_ID": CALLBACK_CREDENTIAL,
            "POINTS_METER_HTTP_HOST": POINTS_HOST,
            "POINTS_METER_HTTP_PORT": str(POINTS_PORT),
            "JOINT_DEBUG_CAPTURE_PATH": str(self.capture_path),
        }
        if self.points_dsn:
            points_env["MAXKIT_POINTS_DSN"] = self.points_dsn
            points_env["MAXKIT_POINTS_SCHEMA"] = "points"
        start_tmux(
            "points-http",
            REPO_ROOT,
            "uv run python scripts/joint_debug/start_points_http.py",
            points_env,
        )
        headers = {
            "Authorization": f"Bearer {CALLBACK_TOKEN}",
            "credential_id": CALLBACK_CREDENTIAL,
            "Content-Type": "application/json",
        }
        if not wait_post(
            f"http://{POINTS_HOST}:{POINTS_PORT}/internal/v0/meter-events",
            headers,
            timeout=40,
        ):
            log = tmux_capture("points-http")
            self.env_notes.append("points HTTP did not become ready")
            self._fail_all("环境", "积分 HTTP 未起来", extra=log)
            self._write_reports()
            return 2

        self.meter_repo, self.clone_note, self.meter_sha = locate_or_clone_kit_meter()
        self.env_notes.append(self.clone_note)
        if self.meter_sha:
            self.env_notes.append(f"kit-meter full SHA {self.meter_sha} (pin {self.meter_pin})")
        if self.meter_repo is None:
            self._points_smoke()
            self._fail_all(
                "环境",
                "kit-meter 树不可用（需 KIT_METER_REPO_ROOT 指向固定 SHA，"
                f"或可克隆且 HEAD 匹配 KIT_METER_PIN={self.meter_pin}）。"
                "本云不反复重试私仓 clone，不声称联合验收已过。",
            )
            self.hub_issues.append(
                HubIssue(
                    seq="env",
                    title="kit-meter tree unavailable or pin mismatch",
                    detail=self.clone_note,
                    request={
                        "url": KIT_METER_URL,
                        "pin": self.meter_pin,
                        "default_pin": DEFAULT_KIT_METER_PIN,
                    },
                    response={"head": self.meter_sha or None, "root": None},
                )
            )
            self._write_reports()
            return 1

        try:
            psycopg_detail = hub_sync(self.meter_repo)
            self.env_notes.append(f"Hub venv psycopg ok: {psycopg_detail}")
            self.meter_dsn = setup_meter_postgres(self.meter_repo)
            self.env_notes.append(
                "meter PG: local kit_meter_joint; applied 001+003"
                " (and 002 if present, local key table only)"
            )
        except Exception as exc:
            self._fail_all("环境", f"kit-meter 依赖/迁移失败: {exc}")
            self._write_reports()
            return 1

        hub_env = {
            "KIT_METER_PG_DSN": self.meter_dsn,
            "KIT_METER_API_TOKEN": BOOTSTRAP_TOKEN,
            "KIT_METER_POINTS_CALLBACK_URL": f"http://{POINTS_HOST}:{POINTS_PORT}",
            "KIT_METER_POINTS_CALLBACK_TOKEN": CALLBACK_TOKEN,
            "KIT_METER_POINTS_CALLBACK_CREDENTIAL_ID": CALLBACK_CREDENTIAL,
            "KIT_METER_HTTP_HOST": HUB_HOST,
            "KIT_METER_HTTP_PORT": str(HUB_PORT),
        }
        start_tmux(
            "meter-hub",
            self.meter_repo,
            "uv run python -m maxkit_meter.http",
            hub_env,
        )
        time.sleep(1)
        bootstrap = HubClient(f"http://{HUB_HOST}:{HUB_PORT}", BOOTSTRAP_TOKEN)
        # Hub may 401 on GET /; mint is the readiness probe.
        deadline = time.time() + 40
        token = ""
        last_err = ""
        while time.time() < deadline:
            try:
                token = mint_key(bootstrap)
                break
            except Exception as exc:
                last_err = str(exc)
                time.sleep(0.5)
        if not token:
            log = tmux_capture("meter-hub")
            self.hub_issues.append(
                HubIssue(
                    seq="env",
                    title="Hub did not mint local msk key",
                    detail=last_err,
                    request={"path": "/v1alpha/keys", "bootstrap": "(local fake, not callback)"},
                    response={"tmux": log[-4000:]},
                )
            )
            self._fail_all("计量", f"Hub 未起来或 /v1alpha/keys 失败: {last_err}")
            self._write_reports()
            return 1
        self.hub = HubClient(f"http://{HUB_HOST}:{HUB_PORT}", token)
        self.env_notes.append("Hub HTTP 127.0.0.1:8088 with local msk_ (ops scopes)")

        self.cases.append(self.seq_a())
        self.cases.append(self.seq_b())
        self.cases.append(self.seq_c())
        self.cases.append(self.seq_d())
        self.cases.append(self.seq_e1())
        self.cases.append(self.seq_e2())
        self.cases.append(self.seq_f())
        self._write_reports()
        return 0 if all(c.verdict == "PASS" for c in self.cases) else 1

    def _points_smoke(self) -> None:
        """Prove the inbound shell is up even when Hub cannot be cloned."""

        rid = "550e8400-e29b-41d4-a716-446655440000"
        payload = {
            "schema_version": "0",
            "event_id": f"evt_hold_{rid}",
            "kind": "task_hold",
            "occurred_at": "2026-11-09T13:00:00Z",
            "request_id": rid,
            "account_id": JOINT_ACCOUNT,
            "billing": "platform",
            "estimate": {
                "model_id": "demo-text-v0",
                "modality": "text",
                "unit_kind": "tokens",
                "input_units": "100",
                "output_units": "20",
            },
        }
        headers = {
            "Authorization": f"Bearer {CALLBACK_TOKEN}",
            "credential_id": CALLBACK_CREDENTIAL,
        }
        try:
            resp = httpx.post(
                f"http://{POINTS_HOST}:{POINTS_PORT}/internal/v0/meter-events",
                json=payload,
                headers=headers,
                timeout=10.0,
            )
            body = _decode_body(resp)
            note = f"points smoke hold HTTP {resp.status_code} body={body}"
            self.env_notes.append(note)
            _write_json(
                self.evidence / "points-smoke-hold.json",
                {"status": resp.status_code, "body": body},
            )
            if self.points_dsn:
                snap = query_points(self.points_dsn, rid, JOINT_ACCOUNT)
                _write_json(self.evidence / "points-smoke-ledger.json", snap)
                self.env_notes.append(
                    f"points smoke ledger billing={snap.get('billing')} "
                    f"available={snap.get('available')} held={snap.get('held')}"
                )
        except httpx.HTTPError as exc:
            self.env_notes.append(f"points smoke hold failed: {exc}")

    def _fail_all(self, attribution: str, summary: str, extra: str = "") -> None:
        titles = {
            "A": "正常 hold→start→freeze/settle",
            "B": "超时 hold→expire-holds/release",
            "C": "余额不足 rejected + 禁 start",
            "D": "丢 ACK duplicate",
            "E1": "Hub 无 hold 先 release 墓碑；再 hold 须 409",
            "E2": "本地模拟在途 task_hold（非 Hub 端到端）",
            "F": "settle shortfall 停重试",
        }
        for seq, title in titles.items():
            self.cases.append(
                CaseResult(
                    seq=seq,
                    title=title,
                    verdict="FAIL",
                    attribution=attribution,
                    summary=summary,
                    notes=[extra] if extra else [],
                    mode="points-inbound-sim" if seq == "E2" else "hub",
                )
            )

    def pump(self) -> HttpCall:
        assert self.hub is not None
        return self.hub.request("POST", "/v1/ops/dispatch-points-outbox", {})

    def create(self, account_id: str, estimate: dict[str, Any]) -> HttpCall:
        assert self.hub is not None
        return self.hub.request(
            "POST",
            "/v1/tasks",
            {"account_id": account_id, "billing": "platform", "estimate": estimate},
        )

    def hold(self, rid: str) -> HttpCall:
        assert self.hub is not None
        return self.hub.request("POST", f"/v1/tasks/{rid}/hold", {})

    def start(self, rid: str) -> HttpCall:
        assert self.hub is not None
        return self.hub.request("POST", f"/v1/tasks/{rid}/start", {})

    def freeze(self, rid: str, usage: dict[str, Any], estimated: Any = None) -> HttpCall:
        assert self.hub is not None
        return self.hub.request(
            "POST",
            f"/v1/tasks/{rid}/freeze",
            {"usage_event": usage, "estimated": estimated},
        )

    def expire(self, now_iso: str) -> HttpCall:
        assert self.hub is not None
        return self.hub.request("POST", "/v1/ops/expire-holds", {"now": now_iso})

    def get_task(self, rid: str) -> HttpCall:
        assert self.hub is not None
        return self.hub.request("GET", f"/v1/tasks/{rid}", {})

    def snapshot(self, rid: str, account_id: str) -> dict[str, Any]:
        meter = {"task": None, "outbox": []}
        if self.meter_dsn:
            try:
                meter = query_meter(self.meter_dsn, rid)
            except Exception as exc:
                meter = {"error": str(exc)}
        points = {
            "billing": None,
            "entries": [],
            "available": None,
            "held": None,
        }
        if self.points_dsn:
            try:
                points = query_points(self.points_dsn, rid, account_id)
            except Exception as exc:
                points = {"error": str(exc)}
        acks = ack_results(load_capture(self.capture_path, rid))
        return {"meter": meter, "points": points, "acks": acks}

    def seq_a(self) -> CaseResult:
        title = "正常 hold→start→freeze/settle"
        issues: list[HubIssue] = []
        create = self.create(JOINT_ACCOUNT, ESTIMATE_A)
        rid = find_rid(create)
        if not rid:
            issues.append(
                HubIssue("A", "create 无 request_id", create.text, create.request, create.body)
            )
            return self._case(
                "A", title, "FAIL", "计量", "POST /v1/tasks 未返回 request_id", "", issues
            )
        hold = self.hold(rid)
        pump1 = self.pump()
        start = self.start(rid)
        freeze = self.freeze(rid, USAGE_A, estimated=None)
        pump2 = self.pump()
        task = self.get_task(rid)
        snap = self.snapshot(rid, JOINT_ACCOUNT)
        acks = snap["acks"]
        hold_ack = next((a for a in acks if a["kind"] == "task_hold"), None)
        settle_ack = next((a for a in acks if a["kind"] == "task_settlement"), None)
        ok = (
            hold_ack is not None
            and hold_ack.get("result") == "applied"
            and hold_ack.get("snapshot_ref")
            and settle_ack is not None
            and settle_ack.get("result") == "applied"
        )
        if start.status and start.status >= 400:
            issues.append(HubIssue("A", "start 被拒", start.text, start.request, start.body))
        if freeze.status and freeze.status >= 400:
            issues.append(HubIssue("A", "freeze 被拒", freeze.text, freeze.request, freeze.body))
        if not hold_ack:
            issues.append(
                HubIssue(
                    "A",
                    "pump 后积分侧未见 hold 入站",
                    f"dispatch={pump1.body}",
                    pump1.request,
                    pump1.body,
                )
            )
        verdict = "PASS" if ok and not issues else "FAIL"
        attr = "无" if verdict == "PASS" else ("计量" if issues else "积分")
        summary = (
            f"hold applied+snapshot_ref, settle applied, same request_id={rid}"
            if verdict == "PASS"
            else f"期望 hold/settle applied；hold_ack={hold_ack} settle_ack={settle_ack}"
        )
        return self._case(
            "A",
            title,
            verdict,
            attr,
            summary,
            rid,
            issues,
            snap,
            [create, hold, pump1, start, freeze, pump2, task],
        )

    def seq_b(self) -> CaseResult:
        title = "超时 hold→expire-holds/release"
        issues: list[HubIssue] = []
        create = self.create(JOINT_ACCOUNT, ESTIMATE_A)
        rid = find_rid(create)
        if not rid:
            return self._case("B", title, "FAIL", "计量", "create 无 request_id", "", issues)
        hold = self.hold(rid)
        pump1 = self.pump()
        expires = ""
        if isinstance(hold.body, dict):
            expires = str(hold.body.get("hold_expires_at") or "")
        now_iso = iso_plus(901)
        if expires:
            try:
                dt = datetime.fromisoformat(expires.replace("Z", "+00:00")) + timedelta(seconds=1)
                now_iso = dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                pass
        expire = self.expire(now_iso)
        pump2 = self.pump()
        task = self.get_task(rid)
        snap = self.snapshot(rid, JOINT_ACCOUNT)
        acks = snap["acks"]
        hold_ack = next((a for a in acks if a["kind"] == "task_hold"), None)
        release_ack = next((a for a in acks if a["kind"] == "task_release"), None)
        settle_ack = next((a for a in acks if a["kind"] == "task_settlement"), None)
        if expire.status and expire.status >= 400:
            issues.append(
                HubIssue("B", "expire-holds 失败", expire.text, expire.request, expire.body)
            )
        if not release_ack:
            issues.append(
                HubIssue(
                    "B",
                    "expire 后未见 task_release 入站",
                    f"expire={expire.body} dispatch={pump2.body}",
                    expire.request,
                    expire.body,
                )
            )
        ok = (
            hold_ack is not None
            and hold_ack.get("result") == "applied"
            and release_ack is not None
            and release_ack.get("result") == "applied"
            and settle_ack is None
        )
        verdict = "PASS" if ok and not issues else "FAIL"
        attr = "无" if verdict == "PASS" else ("计量" if issues else "积分")
        summary = (
            f"hold applied then release hold_timeout, request_id={rid}"
            if verdict == "PASS"
            else f"期望 release applied 且无 settle；release={release_ack}"
        )
        return self._case(
            "B",
            title,
            verdict,
            attr,
            summary,
            rid,
            issues,
            snap,
            [create, hold, pump1, expire, pump2, task],
        )

    def seq_c(self) -> CaseResult:
        title = "余额不足 rejected + 禁 start，迟到 settle/release 冲突"
        issues: list[HubIssue] = []
        create = self.create(POOR_ACCOUNT, ESTIMATE_C)
        rid = find_rid(create)
        if not rid:
            return self._case("C", title, "FAIL", "计量", "create 无 request_id", "", issues)
        hold = self.hold(rid)
        pump1 = self.pump()
        start = self.start(rid)
        freeze = self.freeze(rid, ESTIMATE_C, estimated=False)
        pump2 = self.pump()
        expire = self.expire(iso_plus(901))
        pump3 = self.pump()
        task = self.get_task(rid)
        snap = self.snapshot(rid, POOR_ACCOUNT)
        acks = snap["acks"]
        hold_ack = next((a for a in acks if a["kind"] == "task_hold"), None)
        release_ack = next((a for a in acks if a["kind"] == "task_release"), None)
        settle_ack = next((a for a in acks if a["kind"] == "task_settlement"), None)
        if hold_ack is None:
            issues.append(HubIssue("C", "未见 hold 入站", pump1.text, pump1.request, pump1.body))
        elif hold_ack.get("result") != "rejected":
            issues.append(
                HubIssue(
                    "C",
                    "积分未 rejected（或 Hub 未把 rejected 当失败）",
                    json.dumps(hold_ack, ensure_ascii=False),
                    hold.request,
                    hold.body,
                )
            )
        start_ok = start.status is not None and start.status >= 400
        if not start_ok:
            issues.append(
                HubIssue(
                    "C",
                    "rejected 后 Hub 仍放行 start（应禁模型）",
                    start.text,
                    start.request,
                    start.body,
                )
            )
        if release_ack and release_ack.get("result") == "applied":
            issues.append(
                HubIssue(
                    "C",
                    "rejected 后 Hub 自造 release（契约禁止凭空退款）",
                    json.dumps(release_ack, ensure_ascii=False),
                    expire.request,
                    expire.body,
                )
            )
        late_conflict = True
        if settle_ack and settle_ack.get("result") in {"applied", "shortfall"}:
            late_conflict = False
        ok = (
            hold_ack is not None
            and hold_ack.get("result") == "rejected"
            and start_ok
            and not (release_ack and release_ack.get("result") == "applied")
            and late_conflict
        )
        verdict = "PASS" if ok else "FAIL"
        attr = "无" if verdict == "PASS" else "计量"
        if hold_ack is None:
            attr = "计量"
        elif hold_ack.get("result") != "rejected":
            attr = "积分"
        summary = (
            f"hold rejected, start 被拒, 无自造 release, request_id={rid}"
            if verdict == "PASS"
            else "rejected 后仍放行模型或自造 release，或 hold 未 rejected"
        )
        notes = [
            f"start={start.status} freeze={freeze.status} expire={expire.status}",
        ]
        return self._case(
            "C",
            title,
            verdict,
            attr,
            summary,
            rid,
            issues,
            snap,
            [create, hold, pump1, start, freeze, pump2, expire, pump3, task],
            notes,
        )

    def seq_d(self) -> CaseResult:
        title = "丢 ACK：同 hold event_id 再 dispatch → duplicate"
        issues: list[HubIssue] = []
        create = self.create(JOINT_ACCOUNT, ESTIMATE_A)
        rid = find_rid(create)
        if not rid:
            return self._case("D", title, "FAIL", "计量", "create 无 request_id", "", issues)
        hold = self.hold(rid)
        pump1 = self.pump()
        snap1 = self.snapshot(rid, JOINT_ACCOUNT)
        hold_ack = next((a for a in snap1["acks"] if a["kind"] == "task_hold"), None)
        event_id = f"evt_hold_{rid}"
        if hold_ack and hold_ack.get("event_id"):
            event_id = str(hold_ack["event_id"])
        reset = {"skipped": "no meter dsn"}
        if self.meter_dsn:
            reset = reset_outbox_pending(self.meter_dsn, rid, event_id)
        pump2 = self.pump()
        task = self.get_task(rid)
        snap = self.snapshot(rid, JOINT_ACCOUNT)
        hold_acks = [a for a in snap["acks"] if a["kind"] == "task_hold"]
        dup = next((a for a in hold_acks if a.get("result") == "duplicate"), None)
        first = next((a for a in hold_acks if a.get("result") == "applied"), None)
        if isinstance(reset, dict) and reset.get("updated") == 0:
            issues.append(
                HubIssue(
                    "D",
                    "无法把 outbox 拨回 pending（无管理 API；SQL 未更新）",
                    json.dumps(reset, ensure_ascii=False),
                    {"event_id": event_id, "sql": "delivery_status=pending"},
                    reset,
                )
            )
        if dup is None:
            issues.append(
                HubIssue(
                    "D",
                    "第二次 dispatch 未见 duplicate ACK",
                    f"pump2={pump2.body} acks={hold_acks}",
                    pump2.request,
                    pump2.body,
                )
            )
        ok = (
            first is not None
            and first.get("snapshot_ref")
            and dup is not None
            and dup.get("original_result") == "applied"
            and dup.get("snapshot_ref") == first.get("snapshot_ref")
        )
        verdict = "PASS" if ok else "FAIL"
        attr = "无" if verdict == "PASS" else "计量"
        if first is None:
            attr = "积分" if hold.status and hold.status < 400 else "计量"
        summary = (
            f"duplicate original_result=applied 且 snapshot_ref 相同, request_id={rid}"
            if verdict == "PASS"
            else "第二次 hold 投递未回 duplicate+原 snapshot_ref"
        )
        return self._case(
            "D",
            title,
            verdict,
            attr,
            summary,
            rid,
            issues,
            snap,
            [create, hold, pump1, pump2, task],
            [f"outbox reset={reset}"],
        )

    def points_inbound(self, envelope: dict[str, Any]) -> HttpCall:
        url = f"http://{POINTS_HOST}:{POINTS_PORT}/internal/v0/meter-events"
        headers = {
            "Authorization": f"Bearer {CALLBACK_TOKEN}",
            "credential_id": CALLBACK_CREDENTIAL,
            "Content-Type": "application/json",
        }
        try:
            resp = httpx.post(url, json=envelope, headers=headers, timeout=15.0)
            return HttpCall(
                method="POST",
                url=url,
                request=envelope,
                status=resp.status_code,
                body=_decode_body(resp),
                text=resp.text,
            )
        except httpx.HTTPError as exc:
            return HttpCall(
                method="POST",
                url=url,
                request=envelope,
                status=None,
                body=None,
                text="",
                error=str(exc),
            )

    def seq_e1(self) -> CaseResult:
        title = "E1 Hub 真实 release：无 hold 先 release；再 hold 须 409"
        issues: list[HubIssue] = []
        create = self.create(self.e1_account, self.e1_estimate)
        rid = find_rid(create)
        self.e1_rid = rid
        if not rid:
            return self._case("E1", title, "FAIL", "计量", "create 无 request_id", "", issues)
        assert self.hub is not None
        probes = []
        for method, path, payload in (
            ("POST", f"/v1/tasks/{rid}/fail", {"reason": "client_abort"}),
            ("POST", f"/v1/tasks/{rid}/cancel", {}),
            ("POST", f"/v1/tasks/{rid}/release", {"reason": "hold_timeout"}),
        ):
            call = self.hub.request(method, path, payload)
            probes.append(call)
            if call.status and call.status < 400:
                break
        expire = self.expire(iso_plus(901))
        probes.append(expire)
        pump1 = self.pump()
        hold = self.hold(rid)
        pump2 = self.pump()
        task = self.get_task(rid)
        snap = self.snapshot(rid, self.e1_account)
        acks = snap["acks"]
        release_ack = next((a for a in acks if a["kind"] == "task_release"), None)
        hold_ack = next((a for a in acks if a["kind"] == "task_hold"), None)
        entries = []
        if isinstance(snap["points"], dict):
            entries = list(snap["points"].get("entries") or [])
        tombstone = bool(
            release_ack
            and release_ack.get("result") == "applied"
            and release_ack.get("effect") == "cancelled_without_hold"
        )
        entries_empty = entries == []
        outbox_rows = []
        if isinstance(snap["meter"], dict):
            outbox_rows = list(snap["meter"].get("outbox") or [])
        release_rows = [
            row
            for row in outbox_rows
            if "release" in str(row.get("kind") or "").lower()
            or str(row.get("event_id") or "").startswith("evt_release_")
        ]
        outbox_ok = bool(release_rows) and all(outbox_row_acked(row) for row in release_rows)
        hub_hold_409 = hold.status == 409
        if release_ack is None:
            issues.append(
                HubIssue(
                    "E1",
                    "Hub 无 hold 时未投递 task_release",
                    json.dumps(
                        [{"status": c.status, "url": c.url, "body": c.body} for c in probes],
                        ensure_ascii=False,
                    ),
                    {"tried": [c.url for c in probes]},
                    pump1.body,
                )
            )
        if hold_ack is not None:
            issues.append(
                HubIssue(
                    "E1",
                    "终态后 Hub 仍把 hold 推到积分（E1 门禁是 Hub 409，不是积分 rejected）",
                    json.dumps(hold_ack, ensure_ascii=False),
                    hold.request,
                    hold.body,
                )
            )
        if not hub_hold_409:
            issues.append(
                HubIssue(
                    "E1",
                    f"终态后再 hold 应为 HTTP 409，实际 status={hold.status}",
                    hold.text,
                    hold.request,
                    hold.body,
                )
            )
        if tombstone and not entries_empty:
            issues.append(
                HubIssue(
                    "E1",
                    "墓碑应 entries=[]（无扣款/无 release 退款分录）",
                    json.dumps(entries, ensure_ascii=False, default=str),
                    None,
                    entries,
                )
            )
        if tombstone and not outbox_ok:
            issues.append(
                HubIssue(
                    "E1",
                    "task_release outbox 未 acked",
                    json.dumps(outbox_rows, ensure_ascii=False, default=str),
                    pump1.request,
                    pump1.body,
                )
            )
        ok = tombstone and entries_empty and outbox_ok and hub_hold_409 and hold_ack is None
        verdict = "PASS" if ok else "FAIL"
        attr = "无" if verdict == "PASS" else "计量"
        if tombstone and entries and not entries_empty:
            attr = "积分"
        summary = (
            f"cancelled_without_hold entries=[] outbox acked; Hub hold 409, rid={rid}"
            if verdict == "PASS"
            else "E1 未满足 Hub 真 release 墓碑+409 门禁（不可用积分 rejected 凑绿）"
        )
        return self._case(
            "E1",
            title,
            verdict,
            attr,
            summary,
            rid,
            issues,
            snap,
            [create, *probes, pump1, hold, pump2, task],
            [f"hold.status={hold.status} ledger_phase/task={task.body}"],
        )

    def seq_e2(self) -> CaseResult:
        title = "E2 本地传输模拟：取消前已在途的 task_hold（非 Hub 端到端）"
        issues: list[HubIssue] = []
        rid = self.e1_rid
        notes = [
            "非Hub端到端：直发积分 POST /internal/v0/meter-events，"
            "模拟 Hub 已终态后仍在途的旧 evt_hold_<rid>。E1 的 409 不能替代本条。"
        ]
        if not rid:
            return self._case(
                "E2",
                title,
                "FAIL",
                "环境",
                "无 E1 request_id，未发送在途 hold",
                "",
                issues,
                notes=notes,
                mode="points-inbound-sim",
            )
        before = (
            query_points(self.points_dsn, rid, self.e1_account)
            if self.points_dsn
            else {"billing": None, "entries": [], "available": None, "held": None}
        )
        envelope = late_hold_envelope(rid, self.e1_account, self.e1_estimate)
        inbound = self.points_inbound(envelope)
        after = (
            query_points(self.points_dsn, rid, self.e1_account)
            if self.points_dsn
            else {"billing": None, "entries": [], "available": None, "held": None}
        )
        snap = {
            "meter": {"task": None, "outbox": [], "note": "E2 does not pump Hub outbox"},
            "points": after,
            "acks": ack_results(load_capture(self.capture_path, rid)),
            "before": before,
            "e2_inbound": {"request": envelope, "response": inbound.body, "status": inbound.status},
        }
        body = inbound.body if isinstance(inbound.body, dict) else {}
        rejected = inbound.status == 200 and body.get("result") == "rejected"
        entries_after = list(after.get("entries") or [])
        hold_debits = [
            e
            for e in entries_after
            if e.get("kind") == "task_hold" and int(e.get("delta") or 0) < 0
        ]
        available_unchanged = before.get("available") == after.get("available")
        held_unchanged = before.get("held") == after.get("held")
        if not rejected:
            issues.append(
                HubIssue(
                    "E2",
                    "在途 hold 直发积分未 rejected",
                    inbound.text,
                    envelope,
                    inbound.body,
                )
            )
        if hold_debits:
            issues.append(
                HubIssue(
                    "E2",
                    "在途 hold 产生新扣款",
                    json.dumps(hold_debits, ensure_ascii=False, default=str),
                    envelope,
                    after,
                )
            )
        ok = rejected and not hold_debits and available_unchanged and held_unchanged
        verdict = "PASS" if ok else "FAIL"
        attr = "无" if verdict == "PASS" else "积分"
        summary = (
            f"直发 evt_hold_{rid} → rejected 且无新扣款（非 Hub e2e）"
            if verdict == "PASS"
            else "在途 hold 未被墓碑拒绝，或发生扣款"
        )
        notes.append(f"before={before} after={after}")
        return self._case(
            "E2",
            title,
            verdict,
            attr,
            summary,
            rid,
            issues,
            snap,
            [inbound],
            notes,
            mode="points-inbound-sim",
        )

    def seq_f(self) -> CaseResult:
        title = "hold 成功后 freeze 超估 → settle shortfall，停重试"
        issues: list[HubIssue] = []
        create = self.create(SHORT_ACCOUNT, ESTIMATE_F)
        rid = find_rid(create)
        if not rid:
            return self._case("F", title, "FAIL", "计量", "create 无 request_id", "", issues)
        hold = self.hold(rid)
        pump1 = self.pump()
        start = self.start(rid)
        freeze = self.freeze(rid, USAGE_F, estimated=False)
        pump2 = self.pump()
        pump3 = self.pump()
        task = self.get_task(rid)
        snap = self.snapshot(rid, SHORT_ACCOUNT)
        acks = snap["acks"]
        hold_ack = next((a for a in acks if a["kind"] == "task_hold"), None)
        settle_acks = [a for a in acks if a["kind"] == "task_settlement"]
        short = next((a for a in settle_acks if a.get("result") == "shortfall"), None)
        if start.status and start.status >= 400:
            issues.append(
                HubIssue("F", "short 账户 start 失败", start.text, start.request, start.body)
            )
        if freeze.status and freeze.status >= 400:
            issues.append(
                HubIssue(
                    "F",
                    "freeze 超估被 Hub 拒绝（未能把 shortfall 用量推到积分）",
                    freeze.text,
                    freeze.request,
                    freeze.body,
                )
            )
        if short is None:
            issues.append(
                HubIssue(
                    "F",
                    "未见 shortfall ACK",
                    json.dumps(settle_acks, ensure_ascii=False),
                    freeze.request,
                    freeze.body,
                )
            )
        retried = False
        outbox = []
        if isinstance(snap["meter"], dict):
            outbox = snap["meter"].get("outbox") or []
            for row in outbox:
                status = str(row.get("delivery_status") or row.get("status") or "")
                kind = str(row.get("kind") or "")
                if "settle" in kind and status in {"pending", "retry", "retrying"}:
                    retried = True
        if retried:
            issues.append(
                HubIssue(
                    "F",
                    "shortfall 后 outbox 仍 pending/retry（应停重试）",
                    json.dumps(outbox, ensure_ascii=False, default=str),
                    pump3.request,
                    pump3.body,
                )
            )
        ok = (
            hold_ack is not None
            and hold_ack.get("result") == "applied"
            and short is not None
            and not retried
        )
        verdict = "PASS" if ok else "FAIL"
        attr = "无" if verdict == "PASS" else ("计量" if issues else "积分")
        if (
            hold_ack
            and hold_ack.get("result") == "applied"
            and short is None
            and freeze.status
            and freeze.status < 400
        ):
            attr = "积分"
        summary = (
            f"settle shortfall 且 outbox 停重试, request_id={rid}"
            if verdict == "PASS"
            else "未见 shortfall 或 Hub 仍重试"
        )
        return self._case(
            "F",
            title,
            verdict,
            attr,
            summary,
            rid,
            issues,
            snap,
            [create, hold, pump1, start, freeze, pump2, pump3, task],
        )

    def _case(
        self,
        seq: str,
        title: str,
        verdict: str,
        attribution: str,
        summary: str,
        rid: str,
        issues: list[HubIssue],
        snap: dict[str, Any] | None = None,
        calls: list[HttpCall] | None = None,
        notes: list[str] | None = None,
        mode: str = "hub",
    ) -> CaseResult:
        snap = snap or {"meter": {}, "points": {}, "acks": []}
        payload = {
            "seq": seq,
            "request_id": rid,
            "mode": mode,
            "hub_http": [asdict(c) for c in (calls or [])],
            "snapshot": _jsonable(snap),
            "hub_issues": [asdict(i) for i in issues],
        }
        _write_json(self.evidence / f"{seq}.json", payload)
        self.hub_issues.extend(issues)
        meter = snap.get("meter") if isinstance(snap, dict) else {}
        points = snap.get("points") if isinstance(snap, dict) else {}
        return CaseResult(
            seq=seq,
            title=title,
            verdict=verdict,
            attribution=attribution,
            summary=summary,
            request_id=rid,
            hub_task=(meter or {}).get("task") if isinstance(meter, dict) else None,
            hub_outbox=(meter or {}).get("outbox") if isinstance(meter, dict) else None,
            points_acks=snap.get("acks") or [],
            points_billing=(points or {}).get("billing") if isinstance(points, dict) else None,
            points_entries=(points or {}).get("entries") if isinstance(points, dict) else [],
            points_balance={
                "available": (points or {}).get("available") if isinstance(points, dict) else None,
                "held": (points or {}).get("held") if isinstance(points, dict) else None,
            },
            hub_http=[asdict(c) for c in (calls or [])],
            hub_issues=[asdict(i) for i in issues],
            notes=notes or [],
            mode=mode,
        )

    def _write_reports(self) -> None:
        run = {
            "at": _now(),
            "points_backend": self.points_backend,
            "clone": self.clone_note,
            "meter_repo": str(self.meter_repo) if self.meter_repo else None,
            "kit_meter_pin": self.meter_pin,
            "kit_meter_sha": self.meter_sha or None,
            "kit_accounts_head": _git_head(REPO_ROOT),
            "env_notes": self.env_notes,
            "cases": [asdict(c) for c in self.cases],
            "hub_issues": [asdict(i) for i in self.hub_issues],
        }
        _write_json(REPORT_JSON, run)
        REPORT_MD.write_text(render_markdown(run), encoding="utf-8")
        print(f"wrote {REPORT_MD}", file=sys.stderr)


def _git_head(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    return (proc.stdout or "").strip()


def render_markdown(run: dict[str, Any]) -> str:
    lines = [
        "# 双账真对打证据（冻结契约 A–F）",
        "",
        f"- 时间（UTC）：`{run.get('at')}`",
        f"- kit-accounts HEAD：`{run.get('kit_accounts_head')}`",
        f"- kit-meter pin：`{run.get('kit_meter_pin')}`",
        f"- kit-meter HEAD（完整 SHA）：`{run.get('kit_meter_sha') or 'UNAVAILABLE'}`",
        f"- kit-meter 树：`{run.get('meter_repo') or 'UNAVAILABLE'}`",
        f"- 积分存储：`{run.get('points_backend')}`",
        f"- clone：{run.get('clone')}",
        "",
        "范围：本机 127.0.0.1；假回调凭据 `local-only-meter-callback`；",
        "不部署、不接真扣费、不用客户 `msk_…` 当回调、不改两仓业务代码、不在 kit-meter 开 PR。",
        "",
        "## 环境",
        "",
    ]
    for note in run.get("env_notes") or []:
        lines.append(f"- {note}")
    lines += ["", "## A–F 一句话", ""]
    for case in run.get("cases") or []:
        lines.append(
            f"- **{case['seq']} {case['verdict']}**（卡点：{case['attribution']}）"
            f" `{case.get('request_id') or '-'}` — {case['summary']}"
        )
    lines += ["", "## 逐条对账", ""]
    for case in run.get("cases") or []:
        lines += [
            f"### {case['seq']} {case['verdict']} — {case['title']}",
            "",
            f"- request_id: `{case.get('request_id') or 'n/a'}`",
            f"- 模式: `{case.get('mode') or 'hub'}`",
            f"- 卡点归属: **{case['attribution']}**",
            f"- 摘要: {case['summary']}",
            "",
            "Hub `meter.tasks`：",
            "",
            "```json",
            json.dumps(_jsonable(case.get("hub_task")), ensure_ascii=False, indent=2, default=str),
            "```",
            "",
            "Hub `points_outbox`：",
            "",
            "```json",
            json.dumps(
                _jsonable(case.get("hub_outbox")), ensure_ascii=False, indent=2, default=str
            ),
            "```",
            "",
            "积分 HTTP 回执：",
            "",
            "```json",
            json.dumps(
                _jsonable(case.get("points_acks")), ensure_ascii=False, indent=2, default=str
            ),
            "```",
            "",
            "积分 `billing_requests` + entries + 余额：",
            "",
            "```json",
            json.dumps(
                {
                    "billing": case.get("points_billing"),
                    "entries": case.get("points_entries"),
                    "balance": case.get("points_balance"),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            "```",
            "",
        ]
        if case.get("notes"):
            lines.append("备注：")
            for note in case["notes"]:
                lines.append(f"- {note}")
            lines.append("")
    lines += ["## Hub 侧问题（现象 + 请求/回执原文）", ""]
    issues = run.get("hub_issues") or []
    if not issues:
        lines.append("本跑未记录 Hub 问题单（若 A–F 全 FAIL 在环境克隆，见上节 clone 说明）。")
        lines.append("")
    for issue in issues:
        lines += [
            f"### {issue.get('seq')}: {issue.get('title')}",
            "",
            issue.get("detail") or "",
            "",
            "请求：",
            "",
            "```json",
            json.dumps(_jsonable(issue.get("request")), ensure_ascii=False, indent=2, default=str),
            "```",
            "",
            "回执：",
            "",
            "```json",
            json.dumps(_jsonable(issue.get("response")), ensure_ascii=False, indent=2, default=str),
            "```",
            "",
        ]
    lines += [
        "## 建议（仅报告，未改业务代码）",
        "",
        "- 若 kit-meter 树不可用：把完整固定 SHA 放到 `KIT_METER_PIN`",
        "  （默认锁 `40967aae` = kit-meter PR #9，不要合），",
        "  `KIT_METER_REPO_ROOT` 的 HEAD 必须与 pin 一致；不一致会报错，",
        "  不会悄悄 checkout。仅当明确 `KIT_METER_CHECKOUT_PIN=1` 才拨 HEAD。",
        "- C：Hub 必须把积分 `result=rejected`（HTTP 200）当成 hold 失败，",
        "  禁止 start/freeze，且不得再发 release 退款。",
        "- D：丢 ACK / 同 `event_id` 重放应回 `duplicate` + `original_result=applied`",
        "  + 原 `snapshot_ref`；Hub 仅在任务仍有效时放行模型。",
        "- E1：Hub 真实无 hold 先 release → 积分 `cancelled_without_hold`、entries=[]、",
        "  outbox acked；终态后再 hold 必须 Hub HTTP 409（不可用积分 rejected 凑绿）。",
        "- E2：本地直发积分 `/internal/v0/meter-events` 模拟在途 `evt_hold_<rid>`，",
        "  须 `result=rejected` 且无新扣款。非 Hub 端到端；E1 的 409 不能替代 E2。",
        "- F：`shortfall` 已落账，Hub 应将 outbox 标终态并停止重试。",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    runner = Runner()
    try:
        return runner.run()
    finally:
        if runner.hub is not None:
            runner.hub.close()
        stop_tmux("points-http", "meter-hub")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        stop_tmux("points-http", "meter-hub")
        raise
