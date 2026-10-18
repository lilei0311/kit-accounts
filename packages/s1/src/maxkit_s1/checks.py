"""L1-01 .. L1-07. One failed assertion fails the run."""

import re

from maxkit_s1.errors import UsageError
from maxkit_s1.models import Aligned, Check, Contract, DeliverableUnit, GlossaryTerm, SourceUnit
from maxkit_s1.textutil import is_lock_marker, normalize_en

_PASS = "PASS"
_FAIL = "FAIL"


def align(
    source: list[SourceUnit],
    deliverable: list[DeliverableUnit],
    contract: Contract,
) -> list[Aligned]:
    source_by: dict[int, SourceUnit] = {}
    for unit in source:
        if unit.idx in source_by:
            raise UsageError(f"duplicate source idx {unit.idx}")
        source_by[unit.idx] = unit

    grouped: dict[int, list[DeliverableUnit]] = {}
    for unit in deliverable:
        grouped.setdefault(unit.idx, []).append(unit)

    rows: list[Aligned] = []
    for idx in sorted(source_by):
        src = source_by[idx]
        lock, allow_empty = resolve_lock(contract, idx, src.text)
        units = grouped.get(idx, [])
        if not units:
            rows.append(
                Aligned(
                    idx=idx,
                    source_en=src.text,
                    deliverable_en=None,
                    zh=None,
                    lock=lock,
                    allow_empty=allow_empty,
                    deliverable_lock=None,
                    kind="missing",
                )
            )
            continue
        seen_primary = False
        for unit in units:
            if contract.allow_split and unit.split_of == idx:
                kind = "split"
            elif not seen_primary:
                kind = "paired"
                seen_primary = True
            else:
                kind = "duplicate"
            rows.append(_from_deliverable(idx, src.text, lock, allow_empty, unit, kind))

    for idx in sorted(set(grouped) - set(source_by)):
        for unit in grouped[idx]:
            lock, allow_empty = resolve_lock(contract, idx, unit.en)
            rows.append(_from_deliverable(idx, None, lock, allow_empty, unit, "extra"))
    return rows


def evaluate(
    source: list[SourceUnit],
    deliverable: list[DeliverableUnit],
    rows: list[Aligned],
    contract: Contract,
    glossary: tuple[GlossaryTerm, ...],
    report_name: str,
) -> tuple[list[Check], list[dict[str, object]]]:
    checks: list[Check] = []
    failed: list[dict[str, object]] = []
    for check, row_failures in (
        _check_count(source, deliverable, contract),
        _check_en(rows),
        _check_zh(rows),
        _check_units(rows),
        _check_lock(rows, glossary),
        _check_glossary(rows, glossary),
        _check_report_name(report_name),
    ):
        checks.append(check)
        failed.extend(row_failures)
    failed.sort(key=lambda row: (int(row["idx"]), str(row["id"]), str(row["detail"])))
    return checks, failed


def resolve_lock(contract: Contract, idx: int, source_text: str) -> tuple[str, bool]:
    mode = "none"
    allow_empty = False
    if contract.auto_lock and is_lock_marker(source_text):
        mode = contract.auto_lock_mode
    stripped = source_text.strip()
    for rule in contract.locks:
        if _rule_matches(rule.idx, rule.pattern, idx, stripped):
            mode = rule.mode
            allow_empty = rule.allow_empty
    if mode != "keep_en":
        allow_empty = False
    return mode, allow_empty


def _rule_matches(
    idx_rule: int | None,
    pattern: re.Pattern[str] | None,
    idx: int,
    text: str,
) -> bool:
    idx_ok = idx_rule is None or idx_rule == idx
    if pattern is None:
        return idx_rule is not None and idx_ok
    matched = pattern.search(text) is not None
    if idx_rule is not None:
        return idx_ok and matched
    return matched


def _from_deliverable(
    idx: int,
    source_en: str | None,
    lock: str,
    allow_empty: bool,
    unit: DeliverableUnit,
    kind: str,
) -> Aligned:
    return Aligned(
        idx=idx,
        source_en=source_en,
        deliverable_en=unit.en,
        zh=unit.zh,
        lock=lock,
        allow_empty=allow_empty,
        deliverable_lock=unit.lock,
        kind=kind,
    )


def _row(idx: int, check_id: str, detail: str) -> dict[str, object]:
    return {"idx": idx, "id": check_id, "detail": detail}


def _check_count(
    source: list[SourceUnit],
    deliverable: list[DeliverableUnit],
    contract: Contract,
) -> tuple[Check, list[dict[str, object]]]:
    source_count = len(source)
    deliverable_count = len(deliverable)
    if contract.allow_split:
        primary = sum(1 for unit in deliverable if unit.split_of is None)
        ok = primary == source_count
        detail = (
            f"source_units={source_count} deliverable_units={deliverable_count} "
            f"primary_units={primary}"
        )
    else:
        ok = source_count == deliverable_count
        detail = f"source_units={source_count} deliverable_units={deliverable_count}"
    return Check("L1-01", _PASS if ok else _FAIL, detail), []


def _check_en(rows: list[Aligned]) -> tuple[Check, list[dict[str, object]]]:
    failed: list[dict[str, object]] = []
    compared = 0
    for row in rows:
        if row.kind == "missing" or row.source_en is None or row.deliverable_en is None:
            continue
        compared += 1
        preserve = row.lock != "none"
        left = normalize_en(row.source_en, preserve_internal=preserve)
        right = normalize_en(row.deliverable_en, preserve_internal=preserve)
        if left != right:
            failed.append(_row(row.idx, "L1-02", "en mismatch"))
    if failed:
        return Check("L1-02", _FAIL, f"en mismatch on {len(failed)} unit(s)"), failed
    return Check("L1-02", _PASS, f"en preserved on {compared} paired unit(s)"), []


def _check_zh(rows: list[Aligned]) -> tuple[Check, list[dict[str, object]]]:
    failed: list[dict[str, object]] = []
    for row in rows:
        if row.zh is None or row.lock == "skip":
            continue
        if row.zh.strip() == "" and not (row.lock == "keep_en" and row.allow_empty):
            failed.append(_row(row.idx, "L1-03", "empty zh"))
    if failed:
        return Check("L1-03", _FAIL, f"empty zh on {len(failed)} unit(s)"), failed
    return Check("L1-03", _PASS, "no empty zh outside skip"), []


def _check_units(rows: list[Aligned]) -> tuple[Check, list[dict[str, object]]]:
    failed: list[dict[str, object]] = []
    for row in rows:
        if row.kind == "missing":
            failed.append(_row(row.idx, "L1-04", "missing unit"))
        elif row.kind == "duplicate":
            failed.append(_row(row.idx, "L1-04", "duplicate idx"))
        elif row.kind == "extra":
            failed.append(_row(row.idx, "L1-04", "extra unit"))
    if failed:
        missing = sum(1 for row in rows if row.kind == "missing")
        extra = sum(1 for row in rows if row.kind == "extra")
        duplicate = sum(1 for row in rows if row.kind == "duplicate")
        detail = f"missing={missing} extra={extra} duplicate={duplicate}"
        return Check("L1-04", _FAIL, detail), failed
    return Check("L1-04", _PASS, "no missing or extra units"), []


def _check_lock(
    rows: list[Aligned],
    glossary: tuple[GlossaryTerm, ...],
) -> tuple[Check, list[dict[str, object]]]:
    failed: list[dict[str, object]] = []
    for row in rows:
        if row.kind == "missing" or row.zh is None:
            continue
        if row.deliverable_lock is not None and row.deliverable_lock != row.lock:
            failed.append(
                _row(
                    row.idx,
                    "L1-05",
                    f"lock mismatch: deliverable={row.deliverable_lock} contract={row.lock}",
                )
            )
        reference = row.source_en if row.source_en is not None else row.deliverable_en or ""
        if row.lock == "keep_en":
            copied = normalize_en(row.zh, preserve_internal=True) == normalize_en(
                reference, preserve_internal=True
            )
            if row.zh.strip() == "":
                if not row.allow_empty:
                    failed.append(_row(row.idx, "L1-05", "keep_en: zh must copy en"))
            elif not copied:
                failed.append(_row(row.idx, "L1-05", "keep_en: zh must copy en"))
        elif row.lock == "skip":
            copied = normalize_en(row.zh, preserve_internal=True) == normalize_en(
                reference, preserve_internal=True
            )
            if row.zh.strip() != "" and not copied:
                failed.append(_row(row.idx, "L1-05", "skip: zh must be empty or copy en"))
        elif row.lock == "glossary":
            hay = normalize_en(reference, preserve_internal=True)
            for term in glossary:
                if term.term_en in hay and term.term_zh not in row.zh:
                    failed.append(
                        _row(
                            row.idx,
                            "L1-05",
                            f"glossary: zh missing {term.term_zh!r} for {term.term_en!r}",
                        )
                    )
    if failed:
        return Check("L1-05", _FAIL, f"lock violated on {len(failed)} row(s)"), failed
    return Check("L1-05", _PASS, "lock rules held"), []


def _check_glossary(
    rows: list[Aligned],
    glossary: tuple[GlossaryTerm, ...],
) -> tuple[Check, list[dict[str, object]]]:
    required = tuple(sorted((term for term in glossary if term.required), key=lambda t: t.term_en))
    failed: list[dict[str, object]] = []
    for row in rows:
        if row.kind == "missing" or row.zh is None:
            continue
        reference = row.source_en if row.source_en is not None else row.deliverable_en or ""
        hay = normalize_en(reference, preserve_internal=row.lock != "none")
        for term in required:
            if term.term_en in hay and term.term_zh not in row.zh:
                failed.append(_row(row.idx, "L1-06", f"missing required term {term.term_zh!r}"))
    if failed:
        return Check("L1-06", _FAIL, f"required term missing on {len(failed)} row(s)"), failed
    return Check("L1-06", _PASS, "required glossary terms present"), []


def _check_report_name(report_name: str) -> tuple[Check, list[dict[str, object]]]:
    if report_name.endswith(".l1.json") and report_name != ".l1.json":
        return Check("L1-07", _PASS, f"wrote {report_name}"), []
    return Check("L1-07", _FAIL, "report name must end with .l1.json"), []
