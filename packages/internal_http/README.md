# maxkit-internal-http

Local / staging inbound for Hub→points `POST /internal/v0/meter-events`.
It authenticates a dedicated callback credential and hands the envelope to
`MeterEventAdapter`. **Not** production, **not** an outbox, **not** real
charging.

See [`docs/hub-points-callback.md`](../../docs/hub-points-callback.md) for
the listen URL, env names, and a curl hold.
