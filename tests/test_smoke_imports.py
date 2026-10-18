def test_points_and_identity_import():
    import maxkit_s1
    from maxkit_identity import IdentityService
    from maxkit_points import PointsEngine

    assert PointsEngine.__name__ == "PointsEngine"
    assert IdentityService.__name__ == "IdentityService"
    assert maxkit_s1.run_l1.__name__ == "run_l1"
