"""Shared fixture helpers for synthetic S1 L1 cases."""

import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
CHECK_IDS = ("L1-01", "L1-02", "L1-03", "L1-04", "L1-05", "L1-06", "L1-07")
REPORT_KEYS = {
    "run_id",
    "contract_id",
    "template",
    "source_hash",
    "deliverable_hash",
    "checked_at",
    "result",
    "counts",
    "checks",
    "failed_rows",
}


@pytest.fixture
def pass_a(tmp_path: Path) -> Path:
    dest = tmp_path / "pass_a"
    shutil.copytree(FIXTURES / "pass_a", dest)
    return dest


@pytest.fixture
def pass_b(tmp_path: Path) -> Path:
    dest = tmp_path / "pass_b"
    shutil.copytree(FIXTURES / "pass_b", dest)
    return dest


def checks_by_id(report: dict) -> dict[str, dict]:
    return {check["id"]: check for check in report["checks"]}


@pytest.fixture
def schema():
    return assert_schema


@pytest.fixture
def failed():
    return assert_failed


@pytest.fixture
def by_id():
    return checks_by_id


def assert_schema(report: dict) -> None:
    assert set(report) == REPORT_KEYS
    assert set(report["counts"]) == {"source_units", "deliverable_units", "fail"}
    assert [check["id"] for check in report["checks"]] == list(CHECK_IDS)
    for check in report["checks"]:
        assert set(check) == {"id", "result", "detail"}
        assert check["result"] in {"PASS", "FAIL"}
    for row in report["failed_rows"]:
        assert set(row) == {"idx", "id", "detail"}
        assert isinstance(row["idx"], int)
    assert report["source_hash"].startswith("sha256:")
    assert report["deliverable_hash"].startswith("sha256:")
    assert report["checked_at"].endswith("+08:00")
    assert report["counts"]["fail"] == sum(
        1 for check in report["checks"] if check["result"] == "FAIL"
    )
    assert report["result"] == ("PASS" if report["counts"]["fail"] == 0 else "FAIL")


def assert_failed(report: dict, failed_ids: set[str]) -> None:
    by_id = checks_by_id(report)
    for check_id in CHECK_IDS:
        expect = "FAIL" if check_id in failed_ids else "PASS"
        assert by_id[check_id]["result"] == expect, check_id
    assert {row["id"] for row in report["failed_rows"]} == failed_ids - {"L1-01", "L1-07"}
