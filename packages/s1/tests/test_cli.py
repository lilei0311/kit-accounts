"""CLI exit codes and the console script."""

import subprocess
import sys
from pathlib import Path

from maxkit_s1.cli import main
from maxkit_s1.errors import UsageError


def _args(case: Path, *extra: str) -> list[str]:
    return [
        "--contract",
        str(case / "contract.yaml"),
        "--source",
        str(case / "source.txt"),
        "--deliverable",
        str(case / "deliverable.txt"),
        "--glossary",
        str(case / "glossary.csv"),
        *extra,
    ]


def test_cli_pass_writes_one_stdout_line(pass_a, capsys):
    code = main(_args(pass_a))
    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert " PASS " in captured.out
    assert (pass_a / "deliverable.l1.json").is_file()


def test_cli_fail_exit_one(pass_a, capsys):
    path = pass_a / "deliverable.txt"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    code = main(_args(pass_a))
    captured = capsys.readouterr()
    assert code == 1
    assert " FAIL " in captured.out
    assert captured.out.count("\n") == 1


def test_cli_usage_exit_two(tmp_path, capsys):
    code = main(["--contract", str(tmp_path / "missing.yaml")])
    captured = capsys.readouterr()
    assert code == 2
    assert "maxkit-s1-l1:" in captured.err
    assert captured.out == ""


def test_template_c_is_usage_error(tmp_path):
    contract = tmp_path / "contract.yaml"
    source = tmp_path / "source.txt"
    deliverable = tmp_path / "deliverable.txt"
    contract.write_text("contract_id: S1-FC-20261018-c\ntemplate: C\n", encoding="utf-8")
    source.write_text("Alpha one stays.\n", encoding="utf-8")
    deliverable.write_text("Alpha one stays.\t甲。\n", encoding="utf-8")
    code = main(
        [
            "--contract",
            str(contract),
            "--source",
            str(source),
            "--deliverable",
            str(deliverable),
        ]
    )
    assert code == 2
    assert not (tmp_path / "deliverable.l1.json").exists()


def test_markdown_without_frontmatter(tmp_path):
    contract = tmp_path / "contract.md"
    source = tmp_path / "source.txt"
    deliverable = tmp_path / "deliverable.txt"
    contract.write_text("# no frontmatter\n", encoding="utf-8")
    source.write_text("Alpha one stays.\n", encoding="utf-8")
    deliverable.write_text("Alpha one stays.\t甲。\n", encoding="utf-8")
    code = main(
        [
            "--contract",
            str(contract),
            "--source",
            str(source),
            "--deliverable",
            str(deliverable),
        ]
    )
    assert code == 2


def test_module_and_script_entry(pass_a):
    module = subprocess.run(
        [sys.executable, "-m", "maxkit_s1.l1", *_args(pass_a)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert module.returncode == 0
    assert module.stdout.count("\n") == 1

    script = Path(sys.executable).with_name("maxkit-s1-l1")
    if script.is_file():
        completed = subprocess.run(
            [str(script), *_args(pass_a, "--out", str(pass_a / "again.l1.json"))],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0
        assert (pass_a / "again.l1.json").is_file()


def test_import_surface():
    import maxkit_s1

    assert maxkit_s1.__version__ == "0.1.0"
    assert issubclass(maxkit_s1.UsageError, Exception)
    assert maxkit_s1.UsageError is UsageError
