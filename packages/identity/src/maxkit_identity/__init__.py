"""Minimal identity primitives: person, org, session, and external connector links."""

from maxkit_identity.connectors import AssertionConnector, IdentityConnector
from maxkit_identity.errors import (
    EmailTaken,
    IdentityError,
    InvalidAssertion,
    InvalidCode,
    InvalidIdentityArgument,
    InvalidSession,
    LinkConflict,
    OrgFrozen,
    OrgNotFound,
    PersonFrozen,
    PersonNotFound,
    UnknownSubject,
)
from maxkit_identity.models import (
    ExternalLink,
    IssuedCode,
    Membership,
    Org,
    Person,
    Session,
)
from maxkit_identity.service import IdentityService
from maxkit_identity.store import IdentityStore, MemoryIdentityStore

__all__ = [
    "AssertionConnector",
    "EmailTaken",
    "ExternalLink",
    "IdentityConnector",
    "IdentityError",
    "IdentityService",
    "IdentityStore",
    "InvalidAssertion",
    "InvalidCode",
    "InvalidIdentityArgument",
    "InvalidSession",
    "IssuedCode",
    "LinkConflict",
    "Membership",
    "MemoryIdentityStore",
    "Org",
    "OrgFrozen",
    "OrgNotFound",
    "Person",
    "PersonFrozen",
    "PersonNotFound",
    "Session",
    "UnknownSubject",
]

__version__ = "0.1.0"
