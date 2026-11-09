"""Script-only tests for joint-debug helpers. Does not run Hub×points A–F."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "joint_debug"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import harness  # noqa: E402
import run_af  # noqa: E402


def test_jsonable_pg_numeric_is_decimal_string_not_float() -> None:
    value = Decimal("1.2500")
    dumped = harness.jsonable(value)
    assert dumped == "1.2500"
    assert type(dumped) is str
    nested = harness.jsonable(
        {
            "points": Decimal("4"),
            "available": Decimal("999999.00"),
            "entries": [{"delta": Decimal("-1.50")}],
        }
    )
    assert nested["points"] == "4"
    assert nested["available"] == "999999.00"
    assert nested["entries"][0]["delta"] == "-1.50"
    with pytest.raises(TypeError):
        json.dumps({"points": Decimal("1.25")})
    text = harness.dumps_json({"points": Decimal("1.25")})
    parsed = json.loads(text)
    assert parsed["points"] == "1.25"
    assert isinstance(parsed["points"], str)


def test_jsonable_tiny_decimal_not_scientific_float() -> None:
    dumped = harness.jsonable(Decimal("1E-7"))
    assert dumped == "0.0000001"
    assert "e" not in dumped.lower()
    assert dumped != 1e-7


def test_jsonable_datetime_uuid() -> None:
    ts = datetime(2026, 11, 9, 13, 0, tzinfo=UTC)
    uid = UUID("550e8400-e29b-41d4-a716-446655440000")
    out = harness.jsonable({"at": ts, "id": uid})
    assert out["id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert out["at"].startswith("2026-11-09T13:00:00")


def test_expected_pin_default_and_override() -> None:
    assert harness.expected_pin({}) == "40967aae"
    assert harness.expected_pin({"KIT_METER_PIN": "abc1234567890"}) == "abc1234567890"
    with pytest.raises(ValueError, match="too short"):
        harness.expected_pin({"KIT_METER_PIN": "abc"})


def test_sha_matches_pin_prefix_and_full() -> None:
    full = "40967aae" + "f" * 32
    assert harness.sha_matches_pin(full, "40967aae")
    assert harness.sha_matches_pin(full, full)
    assert harness.sha_matches_pin("40967aae", full)
    assert not harness.sha_matches_pin(full, "e80920eb")
    assert not harness.sha_matches_pin("", "40967aae")


def test_require_meter_head_mismatch_errors_without_explicit_checkout(tmp_path: Path) -> None:
    with pytest.raises(harness.PinMismatchError, match="Refusing to checkout"):
        harness.require_meter_head_matches(
            "aaaaaaaa" + "0" * 32,
            "40967aae",
            root=tmp_path,
            allow_checkout=False,
        )
    harness.require_meter_head_matches(
        "aaaaaaaa" + "0" * 32,
        "40967aae",
        root=tmp_path,
        allow_checkout=True,
    )


def test_isolated_child_env_strips_parent_venv_even_from_extra() -> None:
    env = harness.isolated_child_env(
        {"VIRTUAL_ENV": "/should/not/leak", "FOO": "bar"},
        environ={
            "VIRTUAL_ENV": "/parent/.venv",
            "PYTHONPATH": "/parent/lib",
            "PYTHONHOME": "/parent/py",
            "PYTHONSTARTUP": "/tmp/sitecustomize.py",
            "UV_PROJECT_ENVIRONMENT": "/parent/.venv",
            "PATH": "/usr/bin",
            "HOME": "/home/u",
        },
    )
    for key in harness.STRIP_ENV:
        assert key not in env
    assert env["FOO"] == "bar"
    assert "/usr/bin" in env["PATH"]


def test_psycopg_must_live_in_project_venv(tmp_path: Path) -> None:
    repo = tmp_path / "kit-meter"
    venv = repo / ".venv"
    pkg = venv / "lib" / "python3.12" / "site-packages" / "psycopg" / "__init__.py"
    pkg.parent.mkdir(parents=True)
    pkg.write_text("# probe\n", encoding="utf-8")
    ok, detail = harness.psycopg_is_in_project_venv(str(pkg), str(venv), repo)
    assert ok, detail
    parent = tmp_path / "parent" / ".venv" / "lib" / "psycopg" / "__init__.py"
    parent.parent.mkdir(parents=True)
    parent.write_text("# parent\n", encoding="utf-8")
    ok, _ = harness.psycopg_is_in_project_venv(str(parent), str(parent.parents[2]), repo)
    assert not ok


def test_late_hold_envelope_uses_decimal_strings() -> None:
    rid = "550e8400-e29b-41d4-a716-446655440000"
    env = harness.late_hold_envelope(
        rid,
        "acct_joint_debug",
        {
            "model_id": "demo-text-v0",
            "modality": "text",
            "unit_kind": "tokens",
            "input_units": 100,
            "output_units": 20,
        },
    )
    assert env["event_id"] == f"evt_hold_{rid}"
    assert env["kind"] == "task_hold"
    assert env["account_id"] == "acct_joint_debug"
    assert env["billing"] == "platform"
    assert env["estimate"]["input_units"] == "100"
    assert env["estimate"]["output_units"] == "20"
    with pytest.raises(TypeError, match="float"):
        harness.inbound_quantity_strings({"input_units": 1.5, "output_units": 2})


def test_outbox_row_acked() -> None:
    assert harness.outbox_row_acked({"delivery_status": "acked"})
    assert harness.outbox_row_acked({"status": "delivered"})
    assert not harness.outbox_row_acked({"delivery_status": "pending"})
    assert not harness.outbox_row_acked({})


def _init_meter_git(root: Path, message: str = "init") -> tuple[Path, str]:
    repo = root / "kit-meter"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname = 'kit-meter'\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "joint@example.test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "joint"], cwd=repo, check=True)
    subprocess.run(["git", "add", "pyproject.toml"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    return repo, sha


def test_locate_root_mismatch_does_not_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, sha = _init_meter_git(tmp_path)
    monkeypatch.setenv("KIT_METER_REPO_ROOT", str(repo))
    monkeypatch.setenv("KIT_METER_PIN", "40967aae")
    monkeypatch.delenv("KIT_METER_CHECKOUT_PIN", raising=False)
    path, note, got = run_af.locate_or_clone_kit_meter()
    assert path is None
    assert got == sha
    assert "does not match" in note
    assert "Refusing to checkout" in note
    assert harness.git_full_sha(repo) == sha


def test_locate_root_matching_full_sha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, sha = _init_meter_git(tmp_path, "match")
    monkeypatch.setenv("KIT_METER_REPO_ROOT", str(repo))
    monkeypatch.setenv("KIT_METER_PIN", sha)
    monkeypatch.delenv("KIT_METER_CHECKOUT_PIN", raising=False)
    path, note, got = run_af.locate_or_clone_kit_meter()
    assert path == repo.resolve()
    assert got == sha
    assert sha in note


def test_locate_explicit_checkout_moves_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, sha1 = _init_meter_git(tmp_path, "first")
    (repo / "extra.txt").write_text("second\n", encoding="utf-8")
    subprocess.run(["git", "add", "extra.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "second"], cwd=repo, check=True, capture_output=True)
    sha2 = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    assert sha2 != sha1
    monkeypatch.setenv("KIT_METER_REPO_ROOT", str(repo))
    monkeypatch.setenv("KIT_METER_PIN", sha1)
    monkeypatch.setenv("KIT_METER_CHECKOUT_PIN", "1")
    path, note, got = run_af.locate_or_clone_kit_meter()
    assert path == repo.resolve()
    assert got == sha1
    assert harness.git_full_sha(repo) == sha1
    assert sha1[:8] in note or "matches pin" in note
