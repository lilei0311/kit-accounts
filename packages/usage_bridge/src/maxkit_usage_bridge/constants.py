"""Internal usage→points constants.

``POINTS_PER_YUAN`` is a config constant for cost-equivalent conversion.
It is not a public SKU price.
"""

from typing import Final

POINTS_PER_YUAN: Final[int] = 100

BILLABLE_UNIT_KINDS: Final[frozenset[str]] = frozenset({"tokens", "images", "clips", "chars"})

UNBILLED_UNIT_KINDS: Final[frozenset[str]] = frozenset({"seconds"})
