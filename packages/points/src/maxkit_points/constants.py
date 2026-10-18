"""Consume-track filters.

Product SKU prices are not defined here. ``PAID_TIER_ORIGINS`` is the
optional B-tier view over the same single balance (story-clip ``paid_only``).
``daily_claim`` and ``signup_grant`` stay on that ledger and are skipped by
the filter: daily claim is gift/experience, not a paid tier.
"""

from typing import Final

# Matches app-story-clip paid-tier origins. Not a second balance.
PAID_TIER_ORIGINS: Final[frozenset[str]] = frozenset(
    {
        "subscription_grant",
        "admin_adjustment",
        "earned",
    }
)
