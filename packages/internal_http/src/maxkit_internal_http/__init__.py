"""Local/staging HTTP inbound for Hub meter-events.

This package starts a thin Starlette app for
``POST /internal/v0/meter-events``. The usage-bridge adapter stays a
library; this shell authenticates and maps the ACK onto HTTP after the
local points transaction commits. Not a production gateway and not Hub
outbox/push.
"""

from maxkit_internal_http.http import (
    CREDENTIAL_ENV,
    DEFAULT_HOST,
    DEFAULT_PORT,
    LOCAL_DEV_CREDENTIAL_ID,
    LOCAL_DEV_TOKEN,
    METER_EVENTS_PATH,
    TOKEN_ENV,
    MeterHttpConfigError,
    MeterHttpSettings,
    ack_body,
    build_local_runtime,
    create_app,
    main,
)

__all__ = [
    "CREDENTIAL_ENV",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "LOCAL_DEV_CREDENTIAL_ID",
    "LOCAL_DEV_TOKEN",
    "METER_EVENTS_PATH",
    "TOKEN_ENV",
    "MeterHttpConfigError",
    "MeterHttpSettings",
    "ack_body",
    "build_local_runtime",
    "create_app",
    "main",
]

__version__ = "0.1.0"
