"""Caller-facing failures for the S1 L1 checker. CLI maps these to exit 2."""


class UsageError(Exception):
    """Bad arguments, unreadable input, or an unsupported contract template."""
