def test_points_and_identity_import():
    import maxkit_s1
    from maxkit_identity import IdentityService
    from maxkit_internal_http import METER_EVENTS_PATH, create_app
    from maxkit_points import PointsEngine
    from maxkit_usage_bridge import MeterEventAdapter, PointsUsageSink, RateTable

    assert PointsEngine.__name__ == "PointsEngine"
    assert IdentityService.__name__ == "IdentityService"
    assert maxkit_s1.run_l1.__name__ == "run_l1"
    assert RateTable.__name__ == "RateTable"
    assert PointsUsageSink.__name__ == "PointsUsageSink"
    assert MeterEventAdapter.__name__ == "MeterEventAdapter"
    assert METER_EVENTS_PATH == "/internal/v0/meter-events"
    assert callable(create_app)
