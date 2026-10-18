"""Run one L1 check and write the JSON report."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from maxkit_s1.checks import align, evaluate
from maxkit_s1.contract import load_contract
from maxkit_s1.errors import UsageError
from maxkit_s1.glossary import load_glossary_file, merge_glossary
from maxkit_s1.models import Contract, L1Run
from maxkit_s1.report import build_report, dump_report, file_hash, make_run_id
from maxkit_s1.units import parse_deliverable, parse_source

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def run_l1(
    contract_path: Path | str,
    source_path: Path | str,
    deliverable_path: Path | str,
    glossary_path: Path | str | None = None,
    out_path: Path | str | None = None,
    now: datetime | None = None,
) -> L1Run:
    contract_file = _existing_file(contract_path, "contract")
    source_file = _existing_file(source_path, "source")
    deliverable_file = _existing_file(deliverable_path, "deliverable")
    glossary_file = (
        None if glossary_path in (None, "") else _existing_file(glossary_path, "glossary")
    )

    contract = load_contract(contract_file)
    glossary = contract.glossary
    if glossary_file is not None:
        glossary = merge_glossary(glossary, load_glossary_file(glossary_file))
    contract = _with_glossary(contract, glossary)

    source_text, source_hash = _read(source_file)
    deliverable_text, deliverable_hash = _read(deliverable_file)
    source_units = parse_source(contract.template, source_text)
    deliverable_units = parse_deliverable(contract.template, deliverable_text)

    if out_path in (None, ""):
        report_path = deliverable_file.with_suffix(".l1.json")
    else:
        report_path = Path(out_path)
    if report_path.exists() and report_path.is_dir():
        raise UsageError(f"report path is a directory: {report_path}")
    if not report_path.parent.exists():
        raise UsageError(f"report directory does not exist: {report_path.parent}")

    moment = _clock(now)
    rows = align(source_units, deliverable_units, contract)
    checks, failed_rows = evaluate(
        source_units,
        deliverable_units,
        rows,
        contract,
        contract.glossary,
        report_path.name,
    )
    report = build_report(
        run_id=make_run_id(contract.contract_id, source_hash, deliverable_hash, moment),
        contract_id=contract.contract_id,
        template=contract.template,
        source_hash=source_hash,
        deliverable_hash=deliverable_hash,
        now=moment,
        source_units=len(source_units),
        deliverable_units=len(deliverable_units),
        checks=checks,
        failed_rows=failed_rows,
    )
    try:
        report_path.write_text(dump_report(report), encoding="utf-8")
    except OSError as exc:
        raise UsageError(f"cannot write {report_path}: {exc.strerror}") from exc

    counts = report["counts"]
    summary = (
        f"{report['run_id']} {report['result']} contract={report['contract_id']} "
        f"template={report['template']} source_units={counts['source_units']} "
        f"deliverable_units={counts['deliverable_units']} fail={counts['fail']} "
        f"report={report_path}"
    )
    exit_code = 0 if report["result"] == "PASS" else 1
    return L1Run(report=report, report_path=report_path, exit_code=exit_code, summary=summary)


def _with_glossary(contract: Contract, glossary: tuple) -> Contract:
    return Contract(
        contract_id=contract.contract_id,
        template=contract.template,
        source_lang=contract.source_lang,
        target_lang=contract.target_lang,
        pairing=contract.pairing,
        allow_split=contract.allow_split,
        auto_lock=contract.auto_lock,
        auto_lock_mode=contract.auto_lock_mode,
        locks=contract.locks,
        glossary=glossary,
    )


def _existing_file(path: Path | str | None, label: str) -> Path:
    if path is None or path == "":
        raise UsageError(f"{label} path is required")
    file = Path(path)
    if not file.is_file():
        raise UsageError(f"{label} file not found: {file}")
    return file


def _read(path: Path) -> tuple[str, str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise UsageError(f"cannot read {path}: {exc.strerror}") from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UsageError(f"{path} is not utf-8") from exc
    return text, file_hash(raw)


def _clock(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(_SHANGHAI)
    if now.tzinfo is None:
        raise UsageError("now must be timezone-aware")
    return now
