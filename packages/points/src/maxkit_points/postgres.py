"""PostgreSQL PointsStore + billing request lifecycle.

Runtime opens with a least-privilege DSN and schema search_path. It never
creates the schema, never runs DDL on startup, and never reads secrets from
this module. Ops apply migrations under a separate DDL identity.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from maxkit_points.billing_request import BillingRequest
from maxkit_points.models import (
    Entry,
    Hold,
    HoldSlice,
    IdempotencyRecord,
    PointLot,
)

DEFAULT_SCHEMA = "points"


class PostgresPointsStore:
    """Durable ledger + request lifecycle in schema ``points`` (configurable).

    Implements ``PointsStore`` and ``BillingRequestStore``. Nested
    ``transaction()`` joins the outer connection transaction. Concurrent
    duplicate hold/grant keys are serialized with transaction-scoped advisory
    locks so idempotency checks cannot double-debit.
    """

    def __init__(
        self,
        conninfo: str,
        *,
        schema: str = DEFAULT_SCHEMA,
        connection: Any | None = None,
    ) -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - exercised when extra missing
            raise ImportError(
                "maxkit-points postgres support requires psycopg. "
                "Install with: pip install 'maxkit-points[postgres]' "
                "or uv sync --extra postgres"
            ) from exc

        self._psycopg = psycopg
        self._dict_row = dict_row
        schema_name = _require_ident(schema, "schema")
        self._schema = schema_name
        self._conninfo = conninfo
        self._owns_connection = connection is None
        if connection is None:
            self._conn = psycopg.connect(
                conninfo,
                autocommit=False,
                row_factory=dict_row,
                options=f"-csearch_path={schema_name}",
            )
        else:
            self._conn = connection
        self._depth = 0
        with self._conn.cursor() as cur:
            cur.execute(f"SET search_path TO {schema_name}")
        if self._owns_connection:
            self._conn.commit()

    @classmethod
    def from_env(
        cls,
        *,
        dsn_var: str = "MAXKIT_POINTS_DSN",
        schema_var: str = "MAXKIT_POINTS_SCHEMA",
    ) -> PostgresPointsStore:
        """Open from environment. Does not print or log the DSN value."""

        dsn = os.environ.get(dsn_var)
        if not dsn:
            raise RuntimeError(
                f"{dsn_var} is not set. Runtime needs a least-privilege DSN; "
                "migrations are applied separately by ops under the DDL role."
            )
        schema = os.environ.get(schema_var, DEFAULT_SCHEMA)
        return cls(dsn, schema=schema)

    @property
    def schema(self) -> str:
        return self._schema

    def close(self) -> None:
        if self._owns_connection and self._conn is not None and not self._conn.closed:
            self._conn.close()

    def __enter__(self) -> PostgresPointsStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._depth > 0:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self._depth = 1
        try:
            yield
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            self._depth = 0

    def lock_idempotency_key(self, key: str) -> None:
        digest = _advisory_keys(key)
        with self._conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", digest)

    def next_id(self, prefix: str) -> str:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO id_counters (prefix, value)
                VALUES (%s, 1)
                ON CONFLICT (prefix) DO UPDATE
                  SET value = id_counters.value + 1
                RETURNING value
                """,
                (prefix,),
            )
            value = cur.fetchone()["value"]
        return f"{prefix}_{int(value):08d}"

    def save_lot(self, lot: PointLot) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO point_lots (
                    id, owner_id, owner_kind, origin, source, amount, opened,
                    expires_at, granted_at, grant_key
                ) VALUES (
                    %(id)s, %(owner_id)s, %(owner_kind)s, %(origin)s, %(source)s,
                    %(amount)s, %(opened)s, %(expires_at)s, %(granted_at)s, %(grant_key)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    owner_id = EXCLUDED.owner_id,
                    owner_kind = EXCLUDED.owner_kind,
                    origin = EXCLUDED.origin,
                    source = EXCLUDED.source,
                    amount = EXCLUDED.amount,
                    opened = EXCLUDED.opened,
                    expires_at = EXCLUDED.expires_at,
                    granted_at = EXCLUDED.granted_at,
                    grant_key = EXCLUDED.grant_key
                """,
                _lot_params(lot),
            )

    def get_lot(self, lot_id: str) -> PointLot | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM point_lots WHERE id = %s FOR UPDATE",
                (lot_id,),
            )
            row = cur.fetchone()
        return None if row is None else _lot_from_row(row)

    def lots_for_owner(self, owner_id: str) -> list[PointLot]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM point_lots
                WHERE owner_id = %s
                ORDER BY granted_at, id
                FOR UPDATE
                """,
                (owner_id,),
            )
            rows = cur.fetchall()
        return [_lot_from_row(row) for row in rows]

    def all_lots(self) -> list[PointLot]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM point_lots
                ORDER BY granted_at, id
                FOR UPDATE
                """
            )
            rows = cur.fetchall()
        return [_lot_from_row(row) for row in rows]

    def save_hold(self, hold: Hold) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO holds (
                    id, owner_id, owner_kind, amount, status, ref, key,
                    created_at, updated_at, slices
                ) VALUES (
                    %(id)s, %(owner_id)s, %(owner_kind)s, %(amount)s, %(status)s,
                    %(ref)s, %(key)s, %(created_at)s, %(updated_at)s, %(slices)s::jsonb
                )
                ON CONFLICT (id) DO UPDATE SET
                    owner_id = EXCLUDED.owner_id,
                    owner_kind = EXCLUDED.owner_kind,
                    amount = EXCLUDED.amount,
                    status = EXCLUDED.status,
                    ref = EXCLUDED.ref,
                    key = EXCLUDED.key,
                    created_at = EXCLUDED.created_at,
                    updated_at = EXCLUDED.updated_at,
                    slices = EXCLUDED.slices
                """,
                _hold_params(hold),
            )

    def get_hold(self, hold_id: str) -> Hold | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM holds WHERE id = %s FOR UPDATE",
                (hold_id,),
            )
            row = cur.fetchone()
        return None if row is None else _hold_from_row(row)

    def holds_for_owner(self, owner_id: str) -> list[Hold]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM holds
                WHERE owner_id = %s
                ORDER BY created_at, id
                FOR UPDATE
                """,
                (owner_id,),
            )
            rows = cur.fetchall()
        return [_hold_from_row(row) for row in rows]

    def append_entry(self, entry: Entry) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO entries (
                    id, owner_id, lot_id, hold_id, kind, delta, at, key,
                    lot_expires_at, ref
                ) VALUES (
                    %(id)s, %(owner_id)s, %(lot_id)s, %(hold_id)s, %(kind)s,
                    %(delta)s, %(at)s, %(key)s, %(lot_expires_at)s, %(ref)s
                )
                """,
                _entry_params(entry),
            )

    def entries_for_owner(self, owner_id: str) -> list[Entry]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM entries
                WHERE owner_id = %s
                ORDER BY at, id
                """,
                (owner_id,),
            )
            rows = cur.fetchall()
        return [_entry_from_row(row) for row in rows]

    def get_entry_by_key(self, key: str) -> Entry | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT * FROM entries WHERE key = %s", (key,))
            row = cur.fetchone()
        return None if row is None else _entry_from_row(row)

    def get_idempotency(self, key: str) -> IdempotencyRecord | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM idempotency_records WHERE key = %s FOR UPDATE",
                (key,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return IdempotencyRecord(
            key=row["key"],
            scope=row["scope"],
            payload_hash=row["payload_hash"],
            target_id=row["target_id"],
        )

    def put_idempotency(self, record: IdempotencyRecord) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO idempotency_records (key, scope, payload_hash, target_id)
                VALUES (%(key)s, %(scope)s, %(payload_hash)s, %(target_id)s)
                ON CONFLICT (key) DO UPDATE SET
                    scope = EXCLUDED.scope,
                    payload_hash = EXCLUDED.payload_hash,
                    target_id = EXCLUDED.target_id
                """,
                {
                    "key": record.key,
                    "scope": record.scope,
                    "payload_hash": record.payload_hash,
                    "target_id": record.target_id,
                },
            )

    def get_billing_request(self, request_id: str) -> BillingRequest | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM billing_requests WHERE request_id = %s",
                (request_id,),
            )
            row = cur.fetchone()
        return None if row is None else _billing_from_row(row)

    def lock_billing_request(self, request_id: str) -> BillingRequest | None:
        digest = _advisory_keys(f"billing:{request_id}")
        with self._conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", digest)
            cur.execute(
                "SELECT * FROM billing_requests WHERE request_id = %s FOR UPDATE",
                (request_id,),
            )
            row = cur.fetchone()
        return None if row is None else _billing_from_row(row)

    def save_billing_request(self, request: BillingRequest) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT account_id FROM billing_requests WHERE request_id = %s FOR UPDATE",
                (request.request_id,),
            )
            existing = cur.fetchone()
            if existing is not None and existing["account_id"] != request.account_id:
                raise ValueError(
                    f"account binding immutable for {request.request_id}: "
                    f"{existing['account_id']} != {request.account_id}"
                )
            cur.execute(
                """
                INSERT INTO billing_requests (
                    request_id, account_id, status, billing, payload_hash,
                    settle_payload_hash, rate_snapshot, snapshot_ref, hold_ids,
                    points, estimated, shortfall_points, skipped_reason,
                    reject_reason, model_id, unit_kind, available_after, effect,
                    created_at, updated_at
                ) VALUES (
                    %(request_id)s, %(account_id)s, %(status)s, %(billing)s,
                    %(payload_hash)s, %(settle_payload_hash)s, %(rate_snapshot)s::jsonb,
                    %(snapshot_ref)s, %(hold_ids)s::jsonb, %(points)s, %(estimated)s,
                    %(shortfall_points)s, %(skipped_reason)s, %(reject_reason)s,
                    %(model_id)s, %(unit_kind)s, %(available_after)s, %(effect)s::jsonb,
                    %(created_at)s, %(updated_at)s
                )
                ON CONFLICT (request_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    billing = EXCLUDED.billing,
                    payload_hash = EXCLUDED.payload_hash,
                    settle_payload_hash = EXCLUDED.settle_payload_hash,
                    rate_snapshot = EXCLUDED.rate_snapshot,
                    snapshot_ref = EXCLUDED.snapshot_ref,
                    hold_ids = EXCLUDED.hold_ids,
                    points = EXCLUDED.points,
                    estimated = EXCLUDED.estimated,
                    shortfall_points = EXCLUDED.shortfall_points,
                    skipped_reason = EXCLUDED.skipped_reason,
                    reject_reason = EXCLUDED.reject_reason,
                    model_id = EXCLUDED.model_id,
                    unit_kind = EXCLUDED.unit_kind,
                    available_after = EXCLUDED.available_after,
                    effect = EXCLUDED.effect,
                    updated_at = EXCLUDED.updated_at
                """,
                _billing_params(request),
            )


def apply_migrations(
    *,
    conninfo: str,
    runtime_role: str,
    schema: str = DEFAULT_SCHEMA,
    migrations_dir: Path | None = None,
) -> list[str]:
    """Apply packaged SQL migrations as a DDL identity.

    Runtime app processes must not call this. Ops runs it under points_owner
    (or a local test owner role). ``runtime_role`` receives DML grants only.
    """

    import psycopg

    schema_name = _require_ident(schema, "schema")
    role_name = _require_ident(runtime_role, "runtime_role")
    directory = migrations_dir or _packaged_migrations_dir()
    files = sorted(path for path in directory.glob("*.sql") if path.is_file())
    applied: list[str] = []
    with psycopg.connect(conninfo, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {schema_name}")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            for path in files:
                version = path.stem
                cur.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = %s",
                    (version,),
                )
                if cur.fetchone() is not None:
                    continue
                sql = path.read_text(encoding="utf-8").replace("__RUNTIME_ROLE__", role_name)
                # Migration files also insert schema_migrations; strip duplicate
                # bootstrap create that may already exist.
                cur.execute(sql)
                applied.append(version)
        conn.commit()
    return applied


def _packaged_migrations_dir() -> Path:
    return Path(__file__).resolve().parent / "migrations"


def _require_ident(value: str, field: str) -> str:
    text = value.strip()
    if not text or not text.replace("_", "").isalnum() or text[0].isdigit():
        raise ValueError(f"{field} must be a simple SQL identifier")
    return text


def _advisory_keys(key: str) -> tuple[int, int]:
    digest = abs(hash(key))
    # Split into two signed 32-bit ints for pg_advisory_xact_lock(int, int).
    high = (digest >> 31) & 0x7FFFFFFF
    low = digest & 0x7FFFFFFF
    return high, low if low != 0 or high != 0 else (1, 1)


def _lot_params(lot: PointLot) -> dict[str, Any]:
    return {
        "id": lot.id,
        "owner_id": lot.owner_id,
        "owner_kind": lot.owner_kind,
        "origin": lot.origin,
        "source": lot.source,
        "amount": lot.amount,
        "opened": lot.opened,
        "expires_at": lot.expires_at,
        "granted_at": lot.granted_at,
        "grant_key": lot.grant_key,
    }


def _lot_from_row(row: dict[str, Any]) -> PointLot:
    return PointLot(
        id=row["id"],
        owner_id=row["owner_id"],
        owner_kind=row["owner_kind"],
        origin=row["origin"],
        source=row["source"],
        amount=int(row["amount"]),
        opened=int(row["opened"]),
        expires_at=row["expires_at"],
        granted_at=row["granted_at"],
        grant_key=row["grant_key"],
    )


def _hold_params(hold: Hold) -> dict[str, Any]:
    return {
        "id": hold.id,
        "owner_id": hold.owner_id,
        "owner_kind": hold.owner_kind,
        "amount": hold.amount,
        "status": hold.status,
        "ref": hold.ref,
        "key": hold.key,
        "created_at": hold.created_at,
        "updated_at": hold.updated_at,
        "slices": json.dumps([item.to_dict() for item in hold.slices]),
    }


def _hold_from_row(row: dict[str, Any]) -> Hold:
    raw_slices = row["slices"]
    if isinstance(raw_slices, str):
        raw_slices = json.loads(raw_slices)
    slices = [
        HoldSlice(
            lot_id=item["lot_id"],
            amount=int(item["amount"]),
            expires_at=_parse_dt(item.get("expires_at")),
        )
        for item in raw_slices
    ]
    return Hold(
        id=row["id"],
        owner_id=row["owner_id"],
        owner_kind=row["owner_kind"],
        amount=int(row["amount"]),
        status=row["status"],
        slices=slices,
        ref=row["ref"],
        key=row["key"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _entry_params(entry: Entry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "owner_id": entry.owner_id,
        "lot_id": entry.lot_id,
        "hold_id": entry.hold_id,
        "kind": entry.kind,
        "delta": entry.delta,
        "at": entry.at,
        "key": entry.key,
        "lot_expires_at": entry.lot_expires_at,
        "ref": entry.ref,
    }


def _entry_from_row(row: dict[str, Any]) -> Entry:
    return Entry(
        id=row["id"],
        owner_id=row["owner_id"],
        lot_id=row["lot_id"],
        hold_id=row["hold_id"],
        kind=row["kind"],
        delta=int(row["delta"]),
        at=row["at"],
        key=row["key"],
        lot_expires_at=row["lot_expires_at"],
        ref=row["ref"],
    )


def _billing_params(request: BillingRequest) -> dict[str, Any]:
    from datetime import UTC

    now = datetime.now(UTC)
    created = request.created_at or now
    updated = request.updated_at or now
    return {
        "request_id": request.request_id,
        "account_id": request.account_id,
        "status": request.status,
        "billing": request.billing,
        "payload_hash": request.payload_hash,
        "settle_payload_hash": request.settle_payload_hash,
        "rate_snapshot": json.dumps(request.rate_snapshot)
        if request.rate_snapshot is not None
        else None,
        "snapshot_ref": request.snapshot_ref,
        "hold_ids": json.dumps(list(request.hold_ids)),
        "points": request.points,
        "estimated": request.estimated,
        "shortfall_points": request.shortfall_points,
        "skipped_reason": request.skipped_reason,
        "reject_reason": request.reject_reason,
        "model_id": request.model_id,
        "unit_kind": request.unit_kind,
        "available_after": request.available_after,
        "effect": json.dumps(request.effect),
        "created_at": created,
        "updated_at": updated,
    }


def _billing_from_row(row: dict[str, Any]) -> BillingRequest:
    hold_ids = row["hold_ids"]
    if isinstance(hold_ids, str):
        hold_ids = json.loads(hold_ids)
    effect = row["effect"]
    if isinstance(effect, str):
        effect = json.loads(effect)
    rate_snapshot = row["rate_snapshot"]
    if isinstance(rate_snapshot, str):
        rate_snapshot = json.loads(rate_snapshot)
    return BillingRequest(
        request_id=row["request_id"],
        account_id=row["account_id"],
        status=row["status"],
        payload_hash=row["payload_hash"] or "",
        settle_payload_hash=row["settle_payload_hash"],
        rate_snapshot=rate_snapshot,
        snapshot_ref=row["snapshot_ref"],
        hold_ids=list(hold_ids or []),
        points=int(row["points"]),
        estimated=bool(row["estimated"]),
        shortfall_points=int(row["shortfall_points"]),
        skipped_reason=row["skipped_reason"],
        reject_reason=row["reject_reason"],
        model_id=row["model_id"] or "",
        unit_kind=row["unit_kind"] or "",
        available_after=None if row["available_after"] is None else int(row["available_after"]),
        effect=dict(effect or {}),
        billing=row["billing"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)
