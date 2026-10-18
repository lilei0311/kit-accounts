"""Parse EN source files and bilingual deliverables for templates A and B.

Template A deliverable, one non-empty line per unit:

- ``en<TAB>zh`` or ``en || zh``
- indexed form ``idx<TAB>en<TAB>zh`` with optional ``lock`` and ``note``

``note`` may contain ``split_of=<idx>`` when the contract sets ``allow_split``.
The same idx must be repeated; a new idx is an extra unit.

Template B deliverable is CSV. Header names are case-insensitive and must
include ``en`` and ``zh``. Optional columns: ``id``, ``lock``, ``note``.
A template B source may be CSV with an ``en`` column or plain non-empty lines.
"""

import csv
from io import StringIO

from maxkit_s1.errors import UsageError
from maxkit_s1.models import DeliverableUnit, SourceUnit
from maxkit_s1.textutil import is_idx, parse_split_of

_LOCKS = {"none", "keep_en", "skip", "glossary"}


def parse_source(template: str, text: str) -> list[SourceUnit]:
    if template == "B" and _header_has(text, "en"):
        return _csv_source(text)
    if template in {"A", "B"}:
        return _plain_source(text)
    raise UsageError(f"unsupported template {template!r}")


def parse_deliverable(template: str, text: str) -> list[DeliverableUnit]:
    if template == "A":
        return _plain_deliverable(text)
    if template == "B":
        return _csv_deliverable(text)
    raise UsageError(f"unsupported template {template!r}")


def _plain_source(text: str) -> list[SourceUnit]:
    units: list[SourceUnit] = []
    for line in _nonempty_lines(text):
        units.append(SourceUnit(idx=len(units) + 1, text=line))
    return units


def _plain_deliverable(text: str) -> list[DeliverableUnit]:
    lines = _nonempty_lines(text)
    if not lines:
        return []
    separator = _separator(lines)
    indexed = _is_indexed(lines, separator)
    units: list[DeliverableUnit] = []
    for line in lines:
        parts = line.split(separator)
        if indexed:
            units.append(_indexed_line(parts))
        else:
            if len(parts) != 2:
                raise UsageError(
                    "template A line must be en<TAB>zh (or 'en || zh'); "
                    "use idx<TAB>en<TAB>zh<TAB>lock<TAB>note for lock, note, or split_of"
                )
            units.append(
                DeliverableUnit(
                    idx=len(units) + 1,
                    en=parts[0],
                    zh=parts[1],
                    lock=None,
                    split_of=None,
                )
            )
    return units


def _indexed_line(parts: list[str]) -> DeliverableUnit:
    if len(parts) < 3 or len(parts) > 5:
        raise UsageError("indexed template A line must be idx, en, zh, and optional lock and note")
    if not is_idx(parts[0]):
        raise UsageError(f"deliverable idx must be an integer >= 1, got {parts[0]!r}")
    lock = _parse_lock_cell(parts[3]) if len(parts) >= 4 else None
    note = parts[4].strip() if len(parts) == 5 else ""
    return DeliverableUnit(
        idx=int(parts[0].strip()),
        en=parts[1],
        zh=parts[2],
        lock=lock,
        split_of=parse_split_of(note),
    )


def _separator(lines: list[str]) -> str:
    tabbed = ["\t" in line for line in lines]
    if any(tabbed):
        if not all(tabbed):
            raise UsageError("template A deliverable mixes tab and non-tab lines")
        return "\t"
    marked = [" || " in line for line in lines]
    if all(marked):
        return " || "
    raise UsageError("template A deliverable lines must be en<TAB>zh or 'en || zh'")


def _is_indexed(lines: list[str], separator: str) -> bool:
    for line in lines:
        parts = line.split(separator)
        if len(parts) < 3 or not is_idx(parts[0]):
            return False
    return True


def _csv_source(text: str) -> list[SourceUnit]:
    rows, columns = _csv_rows(text, "source")
    if "en" not in columns:
        raise UsageError("template B source csv must include an en column")
    units: list[SourceUnit] = []
    explicit = "id" in columns
    for line_no, row in rows:
        en = row.get("en", "")
        if _blank_row(row):
            continue
        if en.strip() == "":
            continue
        if explicit:
            units.append(SourceUnit(idx=_row_id(row, line_no, "source"), text=en))
        else:
            units.append(SourceUnit(idx=len(units) + 1, text=en))
    return units


def _csv_deliverable(text: str) -> list[DeliverableUnit]:
    if not _header_has(text, "en") or not _header_has(text, "zh"):
        raise UsageError("template B deliverable must be csv with en and zh columns")
    rows, columns = _csv_rows(text, "deliverable")
    units: list[DeliverableUnit] = []
    explicit = "id" in columns
    has_lock = "lock" in columns
    has_note = "note" in columns
    for line_no, row in rows:
        if _blank_row(row):
            continue
        en = row.get("en", "")
        zh = row.get("zh", "")
        if explicit:
            idx = _row_id(row, line_no, "deliverable")
        else:
            idx = len(units) + 1
        lock = _parse_lock_cell(row.get("lock", "")) if has_lock else None
        note = row.get("note", "") if has_note else ""
        units.append(
            DeliverableUnit(
                idx=idx,
                en=en,
                zh=zh,
                lock=lock,
                split_of=parse_split_of(note),
            )
        )
    return units


def _header_has(text: str, column: str) -> bool:
    first = ""
    for line in text.splitlines():
        if line.strip():
            first = line
            break
    if not first:
        return False
    try:
        cells = next(csv.reader([first]))
    except csv.Error:
        return False
    return any(cell.strip().lower() == column for cell in cells)


def _csv_rows(text: str, where: str) -> tuple[list[tuple[int, dict[str, str]]], set[str]]:
    try:
        reader = csv.DictReader(StringIO(text))
    except csv.Error as exc:
        raise UsageError(f"{where} csv is invalid: {exc}") from exc
    if not reader.fieldnames:
        raise UsageError(f"{where} csv has no header")
    canon: dict[str, str] = {}
    for name in reader.fieldnames:
        if name is None or name.strip() == "":
            continue
        key = name.strip().lower()
        if key in canon:
            raise UsageError(f"{where} csv has a duplicate column {key}")
        canon[key] = name
    rows: list[tuple[int, dict[str, str]]] = []
    for offset, raw in enumerate(reader, start=2):
        if raw is None:
            continue
        mapped = {key: (raw.get(original) or "") for key, original in canon.items()}
        rows.append((offset, mapped))
    return rows, set(canon)


def _blank_row(row: dict[str, str]) -> bool:
    return all(value.strip() == "" for value in row.values())


def _row_id(row: dict[str, str], line_no: int, where: str) -> int:
    raw = row.get("id", "")
    if not is_idx(raw):
        raise UsageError(f"{where} csv line {line_no}: id must be an integer >= 1")
    return int(raw.strip())


def _parse_lock_cell(value: str) -> str | None:
    token = value.strip()
    if token == "":
        return None
    if token not in _LOCKS:
        raise UsageError(f"lock must be none, keep_en, skip, or glossary, got {token!r}")
    return token


def _nonempty_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip() != ""]
