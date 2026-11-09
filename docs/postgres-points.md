# PostgreSQL points ledger (persistence slice)

Durable consume-track ledger + request lifecycle in schema **`points`**.
This is **not** a Hub HTTP callback adapter and does **not** authorize
production deployment.

## Roles (ops model)

| Role | Purpose |
|---|---|
| `points_owner` (NOLOGIN) | Owns schema `points` and migration objects. Controlled DDL identity. |
| `points` (LOGIN) | Runtime app role: `CONNECT` + schema `USAGE` + DML on business tables. **No** `CREATE` on `public`, **no** DDL, **no** schema owner. |

Local disposable tests mirror this with `kit_accounts_owner` / `kit_accounts_test`.

## Runtime configuration (no secrets in repo)

```bash
# Least-privilege DSN only — never commit real credentials.
export MAXKIT_POINTS_DSN='postgresql://points@…/points?host=…&port=…'
export MAXKIT_POINTS_SCHEMA=points   # default
```

Python:

```python
from maxkit_points import PointsEngine
from maxkit_points.postgres import PostgresPointsStore

store = PostgresPointsStore.from_env()  # reads DSN; does not log it
engine = PointsEngine(store=store)
# search_path is set to the configured schema; public need not be writable.
```

Runtime **must not**:

- `CREATE SCHEMA`
- run migrations / auto-DDL on startup
- assume `public` is writable
- connect with the DDL owner role

## Migrations (ops DDL identity only)

Packaged SQL: `packages/points/src/maxkit_points/migrations/001_init.sql`.

Prerequisites (ops, already true in production prep): database exists; schema
`points` exists and is owned by `points_owner`; runtime role has `CONNECT` +
`USAGE`.

Apply under the DDL identity (example shape — ops chooses runner):

```bash
# As points_owner (or SET ROLE points_owner), with search_path=points:
# substitute the runtime role name for __RUNTIME_ROLE__ (production: points)
psql "$OWNER_DDL_URL" -v ON_ERROR_STOP=1 \
  -c "SET search_path TO points" \
  -f packages/points/src/maxkit_points/migrations/001_init.sql
```

Helper (still **not** for app startup; local/ops explicit invocation only):

```python
from maxkit_points.postgres import apply_migrations

apply_migrations(conninfo=owner_dsn, runtime_role="points", schema="points")
```

Grants in `001_init.sql`: `SELECT, INSERT, UPDATE` on ledger + billing_request
tables to the runtime role. **No DELETE.** Default privileges for future owner
tables follow the same DML set.

### Forward / reverse notes

- **Forward:** apply `001_init` once under owner. App deploy can roll forward
  independently afterward (runtime only needs DML).
- **Reverse:** no automatic down migration ships in this slice. Rollback of a
  failed empty deploy is drop objects in `points` under owner (business data
  expected empty at first land). After data exists, reverse requires an
  explicit ops restore plan; backups include the DB but restore is untested.
- **Do not** alter `PUBLIC CONNECT` / permissions on unrelated older databases.

## Request lifecycle invariants (design-aligned)

- External identity: **`request_id` only** (globally unique). Transport
  `event_id` is not an accounting key.
- Account binding is immutable per `request_id`.
- Hold freezes a **local rate snapshot** (+ `snapshot_ref`). Settle/release
  price from that snapshot for the verified account. Omitted Hub
  `snapshot_ref` is fine (lost hold ACK); wrong explicit ref conflicts.
- Release before any applied hold → durable **`cancelled` tombstone**, no
  refund; late hold cannot debit.
- Missing-hold settle does **not** guess rates.
- Duplicates return the original financial result/effect/`available_after`,
  including `rejected` and `shortfall`.
- `pending_config` (missing rate card) replays as pending for unchanged
  payload; persisted insufficient-balance stays `rejected`.
- No accounts-side hold timer; Hub owns timeout → release.
- Shortfall is terminal this phase (no automatic later top-up collection).

## What this slice does **not** include

- Hub HTTP callbacks, auth headers, outbox, ACK protocol
- Writes to kit-meter / model-router databases
- Recharge / purchase_credit / product UI
- Production deployment or credential handling

## Pending external callback contract must still supply

When Hub HTTP lands (later): authenticated delivery, stable retry identity
separate from `request_id`, raw usage quantities (not points), `billing`,
optional `snapshot_ref`, `reason` on release, and `meter.estimated`
provenance. Exact URL/headers/payload serialization remain TBD.
