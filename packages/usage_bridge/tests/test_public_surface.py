from pathlib import Path

from maxkit_points import PointsEngine
from maxkit_usage_bridge import POINTS_PER_YUAN, PointsUsageSink, UsageEvent


def test_module_exports_are_stable_for_a_future_fastapi_shell():
    import maxkit_usage_bridge

    assert hasattr(PointsEngine, "apply_hold")
    assert hasattr(PointsEngine, "apply_consume_settle")
    assert hasattr(PointsEngine, "apply_release")
    assert not hasattr(PointsEngine, "settle")
    assert "key" in PointsEngine.apply_hold.__code__.co_varnames
    assert hasattr(PointsUsageSink, "hold")
    assert hasattr(PointsUsageSink, "settle")
    assert hasattr(PointsUsageSink, "release")
    assert POINTS_PER_YUAN == 100
    for name in (
        "POINTS_PER_YUAN",
        "RateTable",
        "PointsUsageSink",
        "UsageEvent",
        "STATIC_CARD_SNAPSHOT",
    ):
        assert name in maxkit_usage_bridge.__all__
    assert UsageEvent.__dataclass_fields__.keys() >= {
        "model_id",
        "modality",
        "units",
        "unit_kind",
    }


def test_no_kit_model_router_or_http_gateway_in_this_package():
    root = Path(__file__).resolve().parents[1] / "src"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    lowered = text.lower()
    assert "kit_model_router" not in lowered
    assert "kit-model-router" not in lowered
    for banned in (
        "fastapi",
        "starlette",
        "uvicorn",
        "flask",
        "django",
        "from react",
        "from vue",
    ):
        assert banned not in lowered


def test_app_wiring_doc_nails_ec2_and_block_layout_direction():
    here = Path(__file__).resolve()
    root = next(
        parent
        for parent in here.parents
        if (parent / "pyproject.toml").exists() and (parent / "packages").exists()
    )
    doc = (root / "docs" / "app-wiring.md").read_text(encoding="utf-8")
    assert "RateTable.from_snapshot" in doc
    assert "sink.hold" in doc
    assert "apply_hold" in doc
    assert "apply_consume_settle" in doc
    assert "apply_release" in doc
    assert "kit-model-router" in doc
    assert "不建 API 仓" in doc
    assert "type" in doc and "props" in doc
    assert "slots" in doc or "children" in doc
    assert "block catalog" in doc
    assert "React/Vue" in doc
