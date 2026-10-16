"""JSON Schema and golden vectors for person, org, and points records."""

from maxkit_contracts.schemas import (
    SCHEMA_NAMES,
    SchemaError,
    load_examples,
    load_points_vectors,
    load_schema,
    validate,
)

__all__ = [
    "SCHEMA_NAMES",
    "SchemaError",
    "load_examples",
    "load_points_vectors",
    "load_schema",
    "validate",
]

__version__ = "0.1.0"
