"""Re-runnable L1 JSON report."""

import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from maxkit_s1.models import Check

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def file_hash(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def make_run_id(contract_id: str, source_hash: str, deliverable_hash: str, now: datetime) -> str:
    shanghai = now.astimezone(_SHANGHAI)
    day = shanghai.strftime("%Y%m%d")
    material = f"{contract_id}|{source_hash}|{deliverable_hash}|{day}".encode()
    digest = hashlib.sha256(material).hexdigest()
    return f"S1-L1-{day}-{digest[:8]}"


def checked_at(now: datetime) -> str:
    return now.astimezone(_SHANGHAI).isoformat(timespec="seconds")


def build_report(
    *,
    run_id: str,
    contract_id: str,
    template: str,
    source_hash: str,
    deliverable_hash: str,
    now: datetime,
    source_units: int,
    deliverable_units: int,
    checks: list[Check],
    failed_rows: list[dict[str, object]],
) -> dict:
    fail = sum(1 for check in checks if check.result == "FAIL")
    return {
        "run_id": run_id,
        "contract_id": contract_id,
        "template": template,
        "source_hash": source_hash,
        "deliverable_hash": deliverable_hash,
        "checked_at": checked_at(now),
        "result": "PASS" if fail == 0 else "FAIL",
        "counts": {
            "source_units": source_units,
            "deliverable_units": deliverable_units,
            "fail": fail,
        },
        "checks": [check.as_dict() for check in checks],
        "failed_rows": failed_rows,
    }


def dump_report(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"
