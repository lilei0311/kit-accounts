"""Tiny text-path demo. Not a server. No kit-model-router import."""

from datetime import UTC, datetime

from maxkit_points import MemoryPointsStore, PointsEngine
from maxkit_usage_bridge import PointsUsageSink, RateTable, UsageEvent


def main() -> None:
    engine = PointsEngine(
        store=MemoryPointsStore(),
        clock=lambda: datetime(2026, 10, 23, tzinfo=UTC),
    )
    engine.apply_grant(owner_id="acct-demo", amount=1000, key="grant-demo")
    sink = PointsUsageSink(engine, RateTable.from_snapshot())
    estimate = UsageEvent(
        model_id="demo-text-v0",
        modality="text",
        units=None,
        unit_kind="tokens",
        input_units=0,
        output_units=10_000,
    )
    held = sink.hold(
        estimate,
        account_id="acct-demo",
        request_id="req-text-demo",
        billing="platform",
    )
    actual = UsageEvent(
        model_id="demo-text-v0",
        modality="text",
        units=None,
        unit_kind="tokens",
        input_units=0,
        output_units=5_000,
    )
    settled = sink.settle(
        actual,
        account_id="acct-demo",
        request_id="req-text-demo",
        billing="platform",
    )
    print(
        f"hold={held.points} settle={settled.points} "
        f"available={engine.available('acct-demo')} status={settled.status}"
    )


if __name__ == "__main__":
    main()
