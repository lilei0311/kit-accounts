from pathlib import Path

from maxkit_internal_http import (
    CREDENTIAL_ENV,
    DEFAULT_HOST,
    DEFAULT_PORT,
    LOCAL_DEV_CREDENTIAL_ID,
    LOCAL_DEV_TOKEN,
    METER_EVENTS_PATH,
    TOKEN_ENV,
)


def _repo_root() -> Path:
    return next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "pyproject.toml").exists() and (parent / "packages").exists()
    )


def test_callback_doc_covers_local_http_listen_and_env():
    doc = (_repo_root() / "docs" / "hub-points-callback.md").read_text(encoding="utf-8")
    assert "不是真投递" in doc
    assert METER_EVENTS_PATH in doc
    assert f"http://{DEFAULT_HOST}:{DEFAULT_PORT}" in doc
    assert TOKEN_ENV in doc
    assert CREDENTIAL_ENV in doc
    assert LOCAL_DEV_TOKEN in doc
    assert LOCAL_DEV_CREDENTIAL_ID in doc
    assert "仅联调" in doc
    assert "msk_" in doc
    assert "KIT_METER_API_TOKEN" in doc
    assert "python -m maxkit_internal_http" in doc


def test_readme_points_at_local_inbound():
    readme = (_repo_root() / "README.md").read_text(encoding="utf-8")
    assert "maxkit-internal-http" in readme
    assert METER_EVENTS_PATH in readme
    assert TOKEN_ENV in readme
