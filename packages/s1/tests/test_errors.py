"""Error paths: every bad input exits 2 with a `maxkit-s1-l1:` error line on stderr, no report."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from maxkit_s1.cli import main

GOOD_CONTRACT = "contract_id: S1-FC-20261018-err\ntemplate: A\nauto_lock: false\n"


def _case(tmp_path: Path, contract: str | bytes = GOOD_CONTRACT, name="contract.yaml") -> list[str]:
    cpath = tmp_path / name
    if isinstance(contract, bytes):
        cpath.write_bytes(contract)
    else:
        cpath.write_text(contract, encoding="utf-8")
    (tmp_path / "source.txt").write_text("Alpha one stays.\n", encoding="utf-8")
    (tmp_path / "deliverable.txt").write_text("Alpha one stays.\t甲一。\n", encoding="utf-8")
    return [
        "--contract", str(cpath),
        "--source", str(tmp_path / "source.txt"),
        "--deliverable", str(tmp_path / "deliverable.txt"),
    ]  # fmt: skip


def _usage_fail(args, capsys, needle: str) -> None:
    code = main(args)
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "maxkit-s1-l1:" in captured.err
    assert needle in captured.err


def test_sanity_good_case_passes(tmp_path, capsys):
    assert main(_case(tmp_path)) == 0
    assert (tmp_path / "deliverable.l1.json").is_file()


@pytest.mark.parametrize("which", ["source", "deliverable", "contract"])
def test_missing_input_file(which, tmp_path, capsys):
    args = _case(tmp_path)
    target = {"source": "source.txt", "deliverable": "deliverable.txt", "contract": "contract.yaml"}
    (tmp_path / target[which]).unlink()
    _usage_fail(args, capsys, f"{which} file not found")


def test_missing_glossary_file(tmp_path, capsys):
    args = _case(tmp_path) + ["--glossary", str(tmp_path / "nope.csv")]
    _usage_fail(args, capsys, "glossary file not found")


def test_input_is_a_directory(tmp_path, capsys):
    args = _case(tmp_path)
    (tmp_path / "source.txt").unlink()
    (tmp_path / "source.txt").mkdir()
    _usage_fail(args, capsys, "source file not found")


def test_non_utf8_source(tmp_path, capsys):
    args = _case(tmp_path)
    (tmp_path / "source.txt").write_bytes(b"\xff\xfe\x00bad")
    _usage_fail(args, capsys, "is not utf-8")


def test_non_utf8_contract(tmp_path, capsys):
    _usage_fail(_case(tmp_path, b"\xff\xfe\x00bad"), capsys, "is not utf-8")


@pytest.mark.parametrize(
    ("contract", "needle"),
    [
        ("", "contract YAML is empty"),
        ("- just\n- a list\n", "must be a mapping"),
        ("contract_id: [unclosed\n", "invalid YAML"),
        ("template: A\n", "contract_id must be a non-empty string"),
        ("contract_id: '  '\ntemplate: A\n", "contract_id must be a non-empty string"),
        ("contract_id: x\n", "template must be A or B"),
        ("contract_id: x\ntemplate: Z\n", "unsupported template"),
        ("contract_id: x\ntemplate: C\n", "template C is not implemented"),
        ("contract_id: x\ntemplate: A\nauto_lock: maybe\n", "auto_lock must be a boolean"),
        ("contract_id: x\ntemplate: A\nauto_lock_mode: glossary\n", "auto_lock_mode"),
        ("contract_id: x\ntemplate: A\nlocks: nope\n", "locks must be a list"),
        ("contract_id: x\ntemplate: A\nlocks: [1]\n", "locks[1] must be a mapping"),
        ("contract_id: x\ntemplate: A\nlocks:\n  - mode: bogus\n    idx: 1\n", "locks[1].mode"),
        ("contract_id: x\ntemplate: A\nlocks:\n  - mode: skip\n", "needs idx or match"),
        ("contract_id: x\ntemplate: A\nlocks:\n  - mode: skip\n    idx: 0\n", "idx must be"),
        ("contract_id: x\ntemplate: A\nlocks:\n  - mode: skip\n    match: '('\n", "valid regex"),
        ("contract_id: x\ntemplate: A\nglossary:\n  - term_en: a\n", "term_zh must be"),
    ],
)
def test_malformed_contract(contract, needle, tmp_path, capsys):
    _usage_fail(_case(tmp_path, contract), capsys, needle)
    assert not (tmp_path / "deliverable.l1.json").exists()


@pytest.mark.parametrize(
    ("csv_text", "needle"),
    [
        ("", "no header"),
        ("term_en,term_zh\nA,甲\n", "missing column"),
        ("term_en,term_zh,required\nA,,yes\n", "term_en and term_zh are required"),
        ("term_en,term_zh,required\nA,甲,perhaps\n", "required must be yes or no"),
    ],
)
def test_malformed_glossary(csv_text, needle, tmp_path, capsys):
    args = _case(tmp_path)
    (tmp_path / "glossary.csv").write_text(csv_text, encoding="utf-8")
    _usage_fail(args + ["--glossary", str(tmp_path / "glossary.csv")], capsys, needle)


def test_report_directory_missing(tmp_path, capsys):
    args = _case(tmp_path) + ["--out", str(tmp_path / "nodir" / "x.l1.json")]
    _usage_fail(args, capsys, "report directory does not exist")


def test_report_path_is_directory(tmp_path, capsys):
    (tmp_path / "out.l1.json").mkdir()
    args = _case(tmp_path) + ["--out", str(tmp_path / "out.l1.json")]
    _usage_fail(args, capsys, "report path is a directory")


def test_missing_required_args_exit_two(capsys):
    assert main([]) == 2
    assert "usage:" in capsys.readouterr().err


def test_unknown_flag_exit_two(tmp_path, capsys):
    assert main([*_case(tmp_path), "--bogus"]) == 2


def test_help_exit_zero(capsys):
    assert main(["--help"]) == 0
    assert "Exit 0 on PASS" in capsys.readouterr().out


def test_subprocess_exit_codes_0_1_2(tmp_path):
    args = _case(tmp_path)
    cmd = [sys.executable, "-m", "maxkit_s1.l1"]

    def run(extra=()):
        return subprocess.run([*cmd, *args, *extra], capture_output=True, text=True, check=False)

    assert run().returncode == 0
    done = run(["--out", str(tmp_path / "bad_name.json")])
    assert done.returncode == 1  # passing deliverable; only L1-07 fails, a check failure not usage
    report = json.loads((tmp_path / "bad_name.json").read_text(encoding="utf-8"))
    failed = [c["id"] for c in report["checks"] if c["result"] != "PASS"]
    assert failed == ["L1-07"]
    (tmp_path / "deliverable.txt").write_text("Alpha one stays.\t\n", encoding="utf-8")
    assert run().returncode == 1
    (tmp_path / "source.txt").unlink()
    done = run()
    assert done.returncode == 2
    assert done.stdout == ""
