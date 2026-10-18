"""Whitespace rules from the format contract.

Leading and trailing whitespace may be trimmed. On rows that are not LOCK,
internal whitespace runs collapse to a single space. LOCK rows keep internal
spaces so a title cannot be silently respaced.
"""

import re

_IDX = re.compile(r"[1-9][0-9]*")
_WS = re.compile(r"\s+")
_SPLIT_OF = re.compile(r"split_of\s*=\s*([1-9][0-9]*)")
_BRACKETS = re.compile(r"^\[\[.*\]\]$")


def is_idx(value: str) -> bool:
    return _IDX.fullmatch(value.strip()) is not None


def parse_idx(value: object, field: str) -> int:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(field)
    if isinstance(value, int):
        if value < 1:
            raise ValueError(field)
        return value
    if isinstance(value, str) and is_idx(value):
        return int(value.strip())
    raise ValueError(field)


def normalize_en(text: str, *, preserve_internal: bool) -> str:
    stripped = text.strip()
    if preserve_internal:
        return stripped
    return _WS.sub(" ", stripped)


def is_lock_marker(text: str) -> bool:
    """Template A markers: `#...`, a full-line `[[...]]`, or an all-caps line."""
    stripped = text.strip()
    if stripped.startswith("#"):
        return True
    if _BRACKETS.fullmatch(stripped):
        return True
    letters = [char for char in stripped if char.isalpha()]
    return bool(letters) and all(char.isupper() for char in letters)


def parse_split_of(note: str) -> int | None:
    match = _SPLIT_OF.search(note)
    if match is None:
        return None
    return int(match.group(1))
