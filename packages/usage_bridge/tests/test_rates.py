from decimal import Decimal

import pytest

from maxkit_usage_bridge import (
    POINTS_PER_YUAN,
    InvalidUsageArgument,
    MissingRateCard,
    RateTable,
    StaticCard,
    compute_points,
)


def test_points_per_yuan_is_internal_constant():
    assert POINTS_PER_YUAN == 100


def test_tokens_use_conservative_max_input_output():
    table = RateTable.from_snapshot()
    # demo-text-v0: input 2, output 8 → max/1e6 = 8e-6 yuan/token
    assert table.yuan_per_unit("demo-text-v0", "tokens") == Decimal("8") / Decimal("1000000")
    assert table.points_for("demo-text-v0", "tokens", 1_000_000) == 800
    assert table.points_for("demo-text-v0", "tokens", 10_000) == 8
    assert table.points_for("demo-text-v0", "tokens", 5_000) == 4
    assert table.points_for("demo-text-v0", "tokens", 1) == 1


def test_images_clips_chars_formulas():
    table = RateTable.from_snapshot()
    assert table.points_for("demo-image-v0", "images", 3) == 12
    assert table.points_for("demo-video-v0", "clips", 2) == 40
    assert table.points_for("demo-tts-v0", "chars", 1_000) == 2
    assert table.points_for("demo-tts-v0", "chars", 1) == 1


def test_ceil_and_min_one_when_units_positive():
    table = RateTable(rates={("m", "images"): Decimal("0.015")})
    assert table.points_for("m", "images", 1) == 2
    assert compute_points(0, Decimal("0.04")) == 0
    assert compute_points(1, Decimal("0.000001")) == 1


def test_missing_or_unbillable_card_refuses():
    table = RateTable.from_snapshot()
    with pytest.raises(MissingRateCard) as err:
        table.points_for("unknown-model", "tokens", 10)
    assert err.value.model_id == "unknown-model"
    assert err.value.unit_kind == "tokens"
    assert table.has_rate("demo-text-v0", "seconds") is False
    with pytest.raises(MissingRateCard):
        table.yuan_per_unit("demo-text-v0", "seconds")


def test_injected_cards_and_invalid_args():
    table = RateTable(
        [
            StaticCard(
                model_id="custom-text",
                price_per_million_input=1,
                price_per_million_output=1,
            )
        ]
    )
    assert table.points_for("custom-text", "tokens", 1_000_000) == 100
    with pytest.raises(InvalidUsageArgument):
        RateTable(points_per_yuan=0)
    with pytest.raises(InvalidUsageArgument):
        compute_points(-1, Decimal("0.01"))
