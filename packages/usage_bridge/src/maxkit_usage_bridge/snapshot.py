"""Frozen STATIC_CARDS field snapshot for tests and the local demo.

Source of the *field names* (not a live dependency): kit-model-router
``STATIC_CARDS`` yuan columns
``price_per_million_input`` / ``price_per_million_output`` /
``price_per_image_yuan`` / ``price_per_clip_yuan`` /
``price_per_1k_chars_yuan``.

Numbers below are internal cost-equivalent fixtures for the v0 bridge.
They are not public SKU prices and not a live catalog pull.
"""

from maxkit_usage_bridge.models import StaticCard

STATIC_CARD_SNAPSHOT: tuple[StaticCard, ...] = (
    StaticCard(
        model_id="demo-text-v0",
        price_per_million_input=2.0,
        price_per_million_output=8.0,
    ),
    StaticCard(
        model_id="demo-image-v0",
        price_per_image_yuan=0.04,
    ),
    StaticCard(
        model_id="demo-video-v0",
        price_per_clip_yuan=0.20,
    ),
    StaticCard(
        model_id="demo-tts-v0",
        price_per_1k_chars_yuan=0.02,
    ),
)
