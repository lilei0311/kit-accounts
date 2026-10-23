import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from maxkit_contracts import SCHEMA_NAMES, SchemaError, load_examples, load_schema, validate

_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "src" / "maxkit_contracts" / "schemas"


def test_schema_names_match_files():
    files = sorted(path.name for path in _SCHEMA_DIR.glob("*.schema.json"))
    assert files == sorted(f"{name}.schema.json" for name in SCHEMA_NAMES)


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_schema_is_draft_2020_12(name: str):
    schema = load_schema(name)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)
    validate(name, load_examples()[name])


def test_examples_cover_every_schema():
    assert set(load_examples()) == set(SCHEMA_NAMES)


def test_person_rejects_unknown_field_and_bad_email():
    person = dict(load_examples()["person"])
    person["nickname"] = "extra"
    with pytest.raises(SchemaError):
        validate("person", person)

    person = dict(load_examples()["person"])
    person["email"] = "not-an-email"
    with pytest.raises(SchemaError):
        validate("person", person)


def test_point_lot_rejects_negative_opened_and_dual_bucket_field():
    lot = dict(load_examples()["point_lot"])
    lot["opened"] = -1
    with pytest.raises(SchemaError):
        validate("point_lot", lot)

    lot = dict(load_examples()["point_lot"])
    lot["paid_opened"] = 1
    with pytest.raises(SchemaError):
        validate("point_lot", lot)

    lot = dict(load_examples()["point_lot"])
    lot["remaining"] = 1
    with pytest.raises(SchemaError):
        validate("point_lot", lot)


def test_entry_delta_is_signed_and_old_kinds_are_gone():
    entry = dict(load_examples()["entry"])
    entry["delta"] = 0
    entry["kind"] = "task_settlement"
    validate("entry", entry)

    entry["delta"] = -5
    entry["kind"] = "task_hold"
    validate("entry", entry)

    entry["delta"] = 0
    entry["kind"] = "usage_shortfall"
    validate("entry", entry)

    entry["kind"] = "grant"
    with pytest.raises(SchemaError):
        validate("entry", entry)


def test_hold_status_uses_held_not_open():
    hold = dict(load_examples()["hold"])
    assert hold["status"] == "held"
    hold["status"] = "open"
    with pytest.raises(SchemaError):
        validate("hold", hold)


def test_schema_files_are_objects():
    for name in SCHEMA_NAMES:
        payload = json.loads((_SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
        assert payload["title"]
        assert payload["additionalProperties"] is False
