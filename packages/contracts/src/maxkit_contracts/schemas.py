"""Load and validate the Phase 1 contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_NAMES = ("person", "org", "point_lot", "entry", "hold")

_ROOT = Path(__file__).resolve().parent


class SchemaError(ValueError):
    """An instance does not match a kit contract."""

    def __init__(self, schema: str, message: str) -> None:
        super().__init__(f"{schema}: {message}")
        self.schema = schema
        self.message = message


def load_schema(name: str) -> dict[str, Any]:
    if name not in SCHEMA_NAMES:
        raise KeyError(f"unknown schema: {name}")
    path = _ROOT / "schemas" / f"{name}.schema.json"
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise SchemaError(name, "schema file must contain an object")
    return data


def load_examples() -> dict[str, Any]:
    return _load_json(_ROOT / "vectors" / "examples.json")


def load_points_vectors() -> dict[str, Any]:
    return _load_json(_ROOT / "vectors" / "points_golden.json")


def validate(name: str, instance: Mapping[str, Any]) -> None:
    errors = sorted(
        _validator(name).iter_errors(dict(instance)),
        key=lambda item: list(item.absolute_path),
    )
    if not errors:
        return
    first = errors[0]
    location = ".".join(str(part) for part in first.absolute_path) or "<root>"
    raise SchemaError(name, f"{location}: {first.message}")


@lru_cache(maxsize=len(SCHEMA_NAMES))
def _validator(name: str) -> Draft202012Validator:
    schema = load_schema(name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise SchemaError(path.name, "expected a JSON object")
    return data
