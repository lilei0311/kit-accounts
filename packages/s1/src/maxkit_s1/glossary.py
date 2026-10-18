"""Glossary CSV: term_en, term_zh, required."""

import csv
from io import StringIO
from pathlib import Path

from maxkit_s1.errors import UsageError
from maxkit_s1.models import GlossaryTerm

_REQUIRED_YES = {"yes", "y", "true", "1"}
_REQUIRED_NO = {"no", "n", "false", "0"}
_COLUMNS = ("term_en", "term_zh", "required")


def parse_required(value: object, *, where: str) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        raise UsageError(f"{where}: required is missing")
    text = str(value).strip().lower()
    if text in _REQUIRED_YES:
        return True
    if text in _REQUIRED_NO:
        return False
    raise UsageError(f"{where}: required must be yes or no, got {value!r}")


def parse_glossary_csv(text: str, *, where: str) -> tuple[GlossaryTerm, ...]:
    reader = csv.DictReader(StringIO(text))
    if not reader.fieldnames:
        raise UsageError(f"{where}: glossary csv has no header")
    canon: dict[str, str] = {}
    for name in reader.fieldnames:
        if name is None:
            continue
        key = name.strip().lower()
        if key in canon:
            raise UsageError(f"{where}: duplicate glossary column {key}")
        canon[key] = name
    missing = [column for column in _COLUMNS if column not in canon]
    if missing:
        joined = ", ".join(missing)
        raise UsageError(f"{where}: glossary csv missing column(s): {joined}")

    terms: dict[str, GlossaryTerm] = {}
    for line_no, raw in enumerate(reader, start=2):
        cells = {key: (raw.get(orig) or "").strip() for key, orig in canon.items()}
        if not any(cells.values()):
            continue
        term_en = cells["term_en"]
        term_zh = cells["term_zh"]
        if term_en == "" or term_zh == "":
            raise UsageError(f"{where}:{line_no}: term_en and term_zh are required")
        terms[term_en] = GlossaryTerm(
            term_en=term_en,
            term_zh=term_zh,
            required=parse_required(cells["required"], where=f"{where}:{line_no}"),
        )
    return tuple(terms.values())


def load_glossary_file(path: Path) -> tuple[GlossaryTerm, ...]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise UsageError(f"cannot read {path}: {exc.strerror}") from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UsageError(f"{path} is not utf-8") from exc
    return parse_glossary_csv(text, where=str(path))


def merge_glossary(
    inline: tuple[GlossaryTerm, ...],
    from_file: tuple[GlossaryTerm, ...],
) -> tuple[GlossaryTerm, ...]:
    """File rows override inline rows with the same term_en."""
    merged: dict[str, GlossaryTerm] = {term.term_en: term for term in inline}
    for term in from_file:
        merged[term.term_en] = term
    return tuple(merged.values())
