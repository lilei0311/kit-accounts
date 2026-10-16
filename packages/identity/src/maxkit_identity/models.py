"""Identity records. Session tokens and verification codes are not stored in these objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

PersonStatus = Literal["active", "frozen"]
OrgStatus = Literal["active", "frozen"]
MemberRole = Literal["owner", "admin", "member"]
CodePurpose = Literal["verify_email", "login"]


def isoformat(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.isoformat()


@dataclass
class Person:
    id: str
    email: str | None
    display_name: str
    status: PersonStatus
    email_verified: bool
    org_ids: list[str]
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "status": self.status,
            "email_verified": self.email_verified,
            "org_ids": list(self.org_ids),
            "created_at": isoformat(self.created_at),
            "updated_at": isoformat(self.updated_at),
        }


@dataclass
class Org:
    id: str
    name: str
    status: OrgStatus
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "created_at": isoformat(self.created_at),
            "updated_at": isoformat(self.updated_at),
        }


@dataclass
class Membership:
    org_id: str
    person_id: str
    role: MemberRole
    created_at: datetime


@dataclass
class Session:
    """Returned to the caller that just opened a session. `token` is shown once."""

    id: str
    person_id: str
    token: str
    expires_at: datetime
    created_at: datetime
    revoked: bool = False


@dataclass
class IssuedCode:
    """Plaintext verification code. Deliver it to the user; do not persist it."""

    email: str
    purpose: CodePurpose
    code: str
    expires_at: datetime


@dataclass
class ExternalLink:
    connector: str
    subject: str
    person_id: str
    linked_at: datetime


@dataclass
class VerificationChallenge:
    email: str
    purpose: CodePurpose
    code_hash: str
    expires_at: datetime
    attempts: int = 0
    consumed: bool = False


@dataclass
class StoredSession:
    id: str
    person_id: str
    token_hash: str
    expires_at: datetime
    created_at: datetime
    revoked: bool = False
