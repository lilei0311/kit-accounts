"""Contrast pairs: one passing deliverable and single-fault failing ones.

Each variant reuses the contract, source and glossary of ``pass_a`` / ``pass_b`` and
differs from the passing deliverable by exactly one fault. All text is fictional.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from maxkit_s1 import run_l1

FIXTURES = Path(__file__).parent / "fixtures"
CONTRAST = FIXTURES / "contrast"
NOW = datetime(2026, 10, 18, 9, 30, tzinfo=UTC)

CASES_A = [
    ("pass.txt", 0, set()),
    ("fail_dropped_line.txt", 1, {"L1-01", "L1-04"}),
    ("fail_en_changed.txt", 1, {"L1-02"}),
    ("fail_empty_zh.txt", 1, {"L1-03"}),
    ("fail_missing_term.txt", 1, {"L1-06"}),
]
CASES_B = [
    ("pass.csv", 0, set()),
    ("fail_dropped_row.csv", 1, {"L1-01", "L1-04"}),
    ("fail_en_changed.csv", 1, {"L1-02"}),
    ("fail_empty_zh.csv", 1, {"L1-03"}),
    ("fail_missing_term.csv", 1, {"L1-06"}),
]


def _run(base: Path, source: str, deliverable: Path, out: Path):
    return run_l1(
        base / "contract.yaml",
        base / source,
        deliverable,
        glossary_path=base / "glossary.csv",
        out_path=out,
        now=NOW,
    )


@pytest.mark.parametrize(("name", "exit_code", "failed_ids"), CASES_A)
def test_template_a_contrast(name, exit_code, failed_ids, tmp_path, schema, failed):
    outcome = _run(FIXTURES / "pass_a", "source.txt", CONTRAST / "a" / name, tmp_path / "o.l1.json")
    assert outcome.exit_code == exit_code
    schema(outcome.report)
    failed(outcome.report, failed_ids)


@pytest.mark.parametrize(("name", "exit_code", "failed_ids"), CASES_B)
def test_template_b_contrast(name, exit_code, failed_ids, tmp_path, schema, failed):
    outcome = _run(FIXTURES / "pass_b", "source.csv", CONTRAST / "b" / name, tmp_path / "o.l1.json")
    assert outcome.exit_code == exit_code
    schema(outcome.report)
    failed(outcome.report, failed_ids)


def test_pass_and_fail_share_source_hash(tmp_path):
    base = FIXTURES / "pass_a"
    ok = _run(base, "source.txt", CONTRAST / "a" / "pass.txt", tmp_path / "ok.l1.json")
    bad = _run(base, "source.txt", CONTRAST / "a" / "fail_en_changed.txt", tmp_path / "bad.l1.json")
    assert ok.report["source_hash"] == bad.report["source_hash"]
    assert ok.report["deliverable_hash"] != bad.report["deliverable_hash"]
    assert ok.report["failed_rows"] == []
    assert len(bad.report["failed_rows"]) == 1
