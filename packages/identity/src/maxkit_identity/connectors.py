"""External identity connector mouth.

Applications own OAuth and chat-login adapters. A connector only turns an assertion
into a stable subject string. The identity service maps that subject onto a Person,
then opens a session for the person. The session is not tied to the connector.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from maxkit_identity.errors import InvalidAssertion


class IdentityConnector(Protocol):
    name: str

    def extract_subject(self, assertion: Mapping[str, str]) -> str:
        """Return the stable external subject. Raise InvalidAssertion when it is missing."""


class AssertionConnector:
    """Reads one string field from an assertion the application has already verified."""

    def __init__(self, name: str, subject_field: str = "subject") -> None:
        if not name.strip():
            raise ValueError("connector name must be non-empty")
        if not subject_field.strip():
            raise ValueError("subject_field must be non-empty")
        self.name = name
        self.subject_field = subject_field

    def extract_subject(self, assertion: Mapping[str, str]) -> str:
        raw = assertion.get(self.subject_field, "")
        subject = raw.strip()
        if not subject:
            raise InvalidAssertion(f"{self.name} assertion missing {self.subject_field}")
        return subject
