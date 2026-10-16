def test_points_and_identity_import():
    from maxkit_identity import IdentityService
    from maxkit_points import PointsEngine

    assert PointsEngine.__name__ == "PointsEngine"
    assert IdentityService.__name__ == "IdentityService"
