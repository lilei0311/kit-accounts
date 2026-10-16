import pytest

from maxkit_guard import MemoryRateLimiter


def test_import_surface():
    import maxkit_guard

    assert maxkit_guard.MemoryRateLimiter is MemoryRateLimiter


def test_fixed_window_blocks_then_resets():
    limiter = MemoryRateLimiter(limit=2, window_seconds=10)
    first = limiter.check("ada", now=0)
    second = limiter.check("ada", now=1)
    blocked = limiter.check("ada", now=2)
    assert first.allowed and first.remaining == 1
    assert second.allowed and second.remaining == 0
    assert blocked.allowed is False
    assert blocked.remaining == 0
    assert blocked.retry_after_seconds == 8
    other = limiter.check("bea", now=2)
    assert other.allowed is True
    reset = limiter.check("ada", now=10)
    assert reset.allowed is True
    assert reset.remaining == 1


def test_rejects_bad_config():
    with pytest.raises(ValueError):
        MemoryRateLimiter(limit=0, window_seconds=10)
    with pytest.raises(ValueError):
        MemoryRateLimiter(limit=True, window_seconds=10)  # type: ignore[arg-type]
    limiter = MemoryRateLimiter(limit=1, window_seconds=1)
    with pytest.raises(ValueError):
        limiter.check("  ")
