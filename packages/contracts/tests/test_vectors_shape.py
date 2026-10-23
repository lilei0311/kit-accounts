import json

from maxkit_contracts import SCHEMA_NAMES, load_points_vectors

_OPS = {
    "apply_grant",
    "apply_hold",
    "apply_release",
    "apply_consume_settle",
    "apply_expiry",
    "balance",
    "lots",
    "entries",
    "hold",
    "set_now",
}
_EXPECT = {"insufficient", "conflict", "hold_state", "invalid", "not_found"}
_ENTRY_KINDS = {
    "signup_grant",
    "subscription_grant",
    "daily_claim",
    "task_hold",
    "task_release",
    "task_settlement",
    "usage_shortfall",
    "expiry",
    "admin_adjustment",
}
_ORIGINS = {
    "signup_grant",
    "subscription_grant",
    "daily_claim",
    "earned",
    "admin_adjustment",
}


def test_vectors_declare_consume_track_provenance():
    document = load_points_vectors()
    assert document["version"] == 2
    assert document["track"] == "consume"
    provenance = document["provenance"]
    assert "yangong" in provenance
    assert "consume" in provenance
    assert "kit-py-golden-gap-2026-10-18" in provenance
    assert "unreadable" not in provenance
    assert document["cases"]
    executed = {case["id"] for case in document["cases"]}
    excluded = {item["id"] for item in document["excluded"]}
    assert excluded.isdisjoint(executed)
    assert {item["track"] for item in document["excluded"]} == {"contest"}
    assert "winner" not in json.dumps(document["cases"])


def test_vector_steps_use_consume_track_ops():
    for case in load_points_vectors()["cases"]:
        assert case["id"]
        assert case["from"]
        assert case["now"]
        assert case["steps"]
        for step in case["steps"]:
            assert step["op"] in _OPS
            if "expect" in step:
                assert step["expect"] in _EXPECT
            if step["op"] == "apply_grant":
                assert step["amount"] > 0
                assert step["origin"] in _ORIGINS
                assert "source" not in step
            if step["op"] == "balance":
                assert step["available"] >= 0
                assert step["held"] >= 0
                assert step["opened"] >= 0
                assert "remaining" not in step
            if step["op"] == "lots":
                for lot in step["lots"]:
                    assert lot["opened"] <= lot["amount"]
                    assert "remaining" not in lot
                    assert "reserved" not in lot
            if step["op"] == "entries":
                for entry in step["entries"]:
                    assert entry["kind"] in _ENTRY_KINDS
                    assert "amount" not in entry


def test_schema_names_stay_on_the_five_records():
    assert SCHEMA_NAMES == ("person", "org", "point_lot", "entry", "hold")
