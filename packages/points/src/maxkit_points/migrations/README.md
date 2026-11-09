# Points schema migrations

Run these as the **DDL owner** (`points_owner`), never as the runtime app
role (`points`). Do not create the schema here — ops already owns
`CREATE SCHEMA points`.

See [docs/postgres-points.md](../../../../../../docs/postgres-points.md) for
role model, grants, runtime env vars, and forward/reverse notes.

`001_init.sql` expects placeholder `__RUNTIME_ROLE__` (production: `points`).
