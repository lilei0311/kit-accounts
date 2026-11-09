-- maxkit-points schema objects for the consume-track ledger + request lifecycle.
--
-- DDL identity: run as the schema owner (ops: points_owner). Never as the
-- runtime app role (ops: points). This script does NOT CREATE SCHEMA and does
-- NOT assume public is writable.
--
-- Prerequisites (ops-owned, not this script):
--   * database exists
--   * schema "points" exists and is owned by the DDL role
--   * runtime role has CONNECT on the database and USAGE on schema points
--
-- After objects exist, grants below give the runtime role DML only.
-- Substitute :runtime_role if your migration runner supports variables;
-- the bundled apply helper substitutes __RUNTIME_ROLE__.

SET search_path TO points;

CREATE TABLE IF NOT EXISTS points.schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS points.point_lots (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    owner_kind TEXT NOT NULL CHECK (owner_kind IN ('person', 'org')),
    origin TEXT NOT NULL,
    source TEXT NOT NULL,
    amount BIGINT NOT NULL CHECK (amount >= 1),
    opened BIGINT NOT NULL CHECK (opened >= 0 AND opened <= amount),
    expires_at TIMESTAMPTZ,
    granted_at TIMESTAMPTZ NOT NULL,
    grant_key TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS point_lots_owner_idx
    ON points.point_lots (owner_id, granted_at, id);

CREATE TABLE IF NOT EXISTS points.holds (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    owner_kind TEXT NOT NULL CHECK (owner_kind IN ('person', 'org')),
    amount BIGINT NOT NULL CHECK (amount >= 1),
    status TEXT NOT NULL CHECK (status IN ('held', 'released', 'settled')),
    ref TEXT,
    key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    slices JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS holds_owner_idx
    ON points.holds (owner_id, created_at, id);

CREATE TABLE IF NOT EXISTS points.entries (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    lot_id TEXT NOT NULL,
    hold_id TEXT,
    kind TEXT NOT NULL,
    delta BIGINT NOT NULL,
    at TIMESTAMPTZ NOT NULL,
    key TEXT NOT NULL,
    lot_expires_at TIMESTAMPTZ,
    ref TEXT,
    CONSTRAINT entries_key_unique UNIQUE (key)
);

CREATE INDEX IF NOT EXISTS entries_owner_idx
    ON points.entries (owner_id, at, id);

CREATE TABLE IF NOT EXISTS points.idempotency_records (
    key TEXT PRIMARY KEY,
    scope TEXT NOT NULL CHECK (scope IN ('grant', 'hold')),
    payload_hash TEXT NOT NULL,
    target_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS points.id_counters (
    prefix TEXT PRIMARY KEY,
    value BIGINT NOT NULL CHECK (value >= 0)
);

-- request_id is the sole external identity. Transport event IDs are not keys.
-- Cancellation tombstone (status=cancelled) blocks a late hold debit.
CREATE TABLE IF NOT EXISTS points.billing_requests (
    request_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'pending_config',
            'held',
            'settled',
            'released',
            'cancelled',
            'rejected',
            'shortfall',
            'skipped'
        )
    ),
    billing TEXT,
    payload_hash TEXT NOT NULL DEFAULT '',
    settle_payload_hash TEXT,
    rate_snapshot JSONB,
    snapshot_ref TEXT,
    hold_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    points BIGINT NOT NULL DEFAULT 0,
    estimated BOOLEAN NOT NULL DEFAULT FALSE,
    shortfall_points BIGINT NOT NULL DEFAULT 0,
    skipped_reason TEXT,
    reject_reason TEXT,
    model_id TEXT NOT NULL DEFAULT '',
    unit_kind TEXT NOT NULL DEFAULT '',
    available_after BIGINT,
    effect JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS billing_requests_account_idx
    ON points.billing_requests (account_id, updated_at);

GRANT SELECT, INSERT, UPDATE ON TABLE
    points.schema_migrations,
    points.point_lots,
    points.holds,
    points.entries,
    points.idempotency_records,
    points.id_counters,
    points.billing_requests
TO __RUNTIME_ROLE__;

-- No DELETE grant: ledger entries are append-oriented; corrections are new rows.
ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA points
    GRANT SELECT, INSERT, UPDATE ON TABLES TO __RUNTIME_ROLE__;

INSERT INTO points.schema_migrations (version)
VALUES ('001_init')
ON CONFLICT (version) DO NOTHING;
