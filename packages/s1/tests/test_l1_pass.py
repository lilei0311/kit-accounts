"""Passing L1 runs on fictional EN→ZH lines."""

import hashlib
import json
from datetime import UTC, datetime

from maxkit_s1 import run_l1

NOW = datetime(2026, 10, 18, 9, 30, tzinfo=UTC)


def test_template_a_pass_and_rerun(pass_a, schema):
    first = run_l1(
        pass_a / "contract.yaml",
        pass_a / "source.txt",
        pass_a / "deliverable.txt",
        glossary_path=pass_a / "glossary.csv",
        now=NOW,
    )
    second = run_l1(
        pass_a / "contract.yaml",
        pass_a / "source.txt",
        pass_a / "deliverable.txt",
        glossary_path=pass_a / "glossary.csv",
        now=NOW,
    )
    assert first.exit_code == 0
    assert first.report_path == pass_a / "deliverable.l1.json"
    assert first.report_path.is_file()
    schema(first.report)
    assert first.report["result"] == "PASS"
    assert first.report["template"] == "A"
    assert first.report["contract_id"] == "S1-FC-20261018-fox"
    assert first.report["counts"] == {"source_units": 7, "deliverable_units": 7, "fail": 0}
    assert first.report["failed_rows"] == []
    assert first.report["checked_at"] == "2026-10-18T17:30:00+08:00"
    assert first.report["run_id"].startswith("S1-L1-20261018-")
    raw = (pass_a / "source.txt").read_bytes()
    assert first.report["source_hash"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    written = json.loads(first.report_path.read_text(encoding="utf-8"))
    assert written == first.report == second.report
    assert "PASS" in first.summary
    assert str(first.report_path) in first.summary


def test_markdown_frontmatter_contract(pass_a):
    outcome = run_l1(
        pass_a / "contract.md",
        pass_a / "source.txt",
        pass_a / "deliverable.txt",
        glossary_path=pass_a / "glossary.csv",
        now=NOW,
    )
    assert outcome.exit_code == 0
    assert outcome.report["contract_id"] == "S1-FC-20261018-fox"


def test_template_b_csv_header_case(pass_b):
    outcome = run_l1(
        pass_b / "contract.yaml",
        pass_b / "source.csv",
        pass_b / "deliverable.csv",
        glossary_path=pass_b / "glossary.csv",
        now=NOW,
    )
    assert outcome.exit_code == 0
    assert outcome.report["template"] == "B"
    assert outcome.report["counts"]["source_units"] == 5
    assert outcome.report["counts"]["deliverable_units"] == 5
    assert outcome.report_path == pass_b / "deliverable.l1.json"


def test_template_b_pairs_by_id_not_row_order(tmp_path):
    contract = tmp_path / "contract.yaml"
    source = tmp_path / "source.csv"
    deliverable = tmp_path / "deliverable.csv"
    contract.write_text(
        "contract_id: S1-FC-20261018-ids\ntemplate: B\nauto_lock: false\n",
        encoding="utf-8",
    )
    source.write_text(
        "id,en\n10,Alpha line one.\n20,Beta line two.\n",
        encoding="utf-8",
    )
    deliverable.write_text(
        "ZH,ID,EN\n乙二,20,Beta line two.\n甲一,10,Alpha line one.\n",
        encoding="utf-8",
    )
    outcome = run_l1(contract, source, deliverable, now=NOW)
    assert outcome.exit_code == 0
    assert outcome.report["failed_rows"] == []


def test_trim_and_internal_space_collapse(tmp_path):
    contract = tmp_path / "contract.yaml"
    source = tmp_path / "source.txt"
    deliverable = tmp_path / "deliverable.txt"
    contract.write_text(
        "contract_id: S1-FC-20261018-ws\ntemplate: A\nauto_lock: false\n",
        encoding="utf-8",
    )
    source.write_text("  The copper   fox crossed.  \n", encoding="utf-8")
    deliverable.write_text("The copper fox crossed.\t铜狐过去了。\n", encoding="utf-8")
    outcome = run_l1(contract, source, deliverable, now=NOW)
    assert outcome.exit_code == 0


def test_lock_keeps_internal_spaces(tmp_path):
    contract = tmp_path / "contract.yaml"
    source = tmp_path / "source.txt"
    deliverable = tmp_path / "deliverable.txt"
    contract.write_text(
        "contract_id: S1-FC-20261018-sp\ntemplate: A\nauto_lock: true\n",
        encoding="utf-8",
    )
    source.write_text("HOLD  FRAME\n", encoding="utf-8")
    deliverable.write_text("HOLD  FRAME\tHOLD  FRAME\n", encoding="utf-8")
    outcome = run_l1(contract, source, deliverable, now=NOW)
    assert outcome.exit_code == 0

    deliverable.write_text("HOLD FRAME\tHOLD FRAME\n", encoding="utf-8")
    collapsed = run_l1(contract, source, deliverable, now=NOW)
    assert collapsed.exit_code == 1
    assert collapsed.report["checks"][1]["id"] == "L1-02"
    assert collapsed.report["checks"][1]["result"] == "FAIL"


def test_allow_split_repeats_idx(tmp_path):
    contract = tmp_path / "contract.yaml"
    source = tmp_path / "source.txt"
    deliverable = tmp_path / "deliverable.txt"
    contract.write_text(
        "contract_id: S1-FC-20261018-split\ntemplate: A\nallow_split: true\nauto_lock: false\n",
        encoding="utf-8",
    )
    source.write_text("Alpha one stays.\nBeta two stays.\n", encoding="utf-8")
    deliverable.write_text(
        "\n".join(
            [
                "1\tAlpha one stays.\t甲一。\tnone\t",
                "2\tBeta two stays.\t乙二。\tnone\t",
                "2\tBeta two stays.\t马上就到。\tnone\tsplit_of=2",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    outcome = run_l1(contract, source, deliverable, now=NOW)
    assert outcome.exit_code == 0
    assert outcome.report["counts"]["source_units"] == 2
    assert outcome.report["counts"]["deliverable_units"] == 3


def test_hash_marker_and_pipe_delimiter(tmp_path):
    contract = tmp_path / "contract.yaml"
    source = tmp_path / "source.txt"
    deliverable = tmp_path / "deliverable.txt"
    contract.write_text(
        "contract_id: S1-FC-20261018-cue\ntemplate: A\nauto_lock: true\n",
        encoding="utf-8",
    )
    source.write_text("# CUE\nHello there.\n", encoding="utf-8")
    deliverable.write_text("# CUE || # CUE\nHello there. || 你好呀。\n", encoding="utf-8")
    outcome = run_l1(contract, source, deliverable, now=NOW)
    assert outcome.exit_code == 0
