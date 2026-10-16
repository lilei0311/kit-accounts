from maxkit_contracts import SCHEMA_NAMES, load_points_vectors

_OPS = {
    "grant",
    "apply_hold",
    "settle",
    "release",
    "expire_due",
    "balance",
    "lots",
    "entries",
    "set_now",
}
_EXPECT = {"insufficient", "conflict", "hold_state", "invalid", "not_found"}


def test_vectors_declare_provenance_gap():
    document = load_points_vectors()
    assert document["version"] == 1
    assert "yangong" in document["provenance"]
    assert document["cases"]


def test_vector_steps_use_known_ops():
    for case in load_points_vectors()["cases"]:
        assert case["id"]
        assert case["now"]
        assert case["steps"]
        for step in case["steps"]:
            assert step["op"] in _OPS
            if "expect" in step:
                assert step["expect"] in _EXPECT
            if step["op"] == "grant":
                assert step["amount"] > 0
            if step["op"] == "balance":
                assert step["available"] + step["held"] <= step["remaining"]


def test_schema_names_stay_on_the_five_records():
    assert SCHEMA_NAMES == ("person", "org", "point_lot", "entry", "hold")
