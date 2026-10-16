"""Person, org, verification code, and session operations.

`require_person` is the framework-free core of an application `require_user`.
A FastAPI wrapper stays in the app. This package does not talk to points.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Literal

from maxkit_contracts import validate
from maxkit_identity.connectors import IdentityConnector
from maxkit_identity.errors import (
    EmailTaken,
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
    CodePurpose,
    ExternalLink,
    IssuedCode,
    MemberRole,
    Membership,
    Org,
    Person,
    Session,
    StoredSession,
    VerificationChallenge,
)
from maxkit_identity.store import IdentityStore, MemoryIdentityStore

_MAX_CODE_ATTEMPTS = 5
_PURPOSES = ("verify_email", "login")
_ROLES = ("owner", "admin", "member")


class IdentityService:
    def __init__(
        self,
        store: IdentityStore | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store or MemoryIdentityStore()
        self._clock = clock or (lambda: datetime.now(UTC))

    def register_person(
        self,
        *,
        display_name: str,
        email: str | None = None,
        person_id: str | None = None,
    ) -> Person:
        with self._store.transaction():
            now = self._now()
            normalized = _normalize_email(email) if email is not None else None
            name = _require_text(display_name, "display_name")
            if normalized is not None and self._store.person_by_email(normalized) is not None:
                raise EmailTaken(normalized)
            person = Person(
                id=person_id.strip() if person_id else _new_id("person"),
                email=normalized,
                display_name=name,
                status="active",
                email_verified=False,
                org_ids=[],
                created_at=now,
                updated_at=now,
            )
            if not person.id:
                raise InvalidIdentityArgument("person_id must be non-empty")
            if self._store.get_person(person.id) is not None:
                raise InvalidIdentityArgument(f"person id already exists: {person.id}")
            validate("person", person.to_dict())
            self._store.save_person(person)
            return person

    def issue_code(
        self,
        *,
        email: str,
        purpose: CodePurpose,
        ttl_seconds: int = 600,
    ) -> IssuedCode:
        """Return a one-time plaintext code for a registered email.

        The caller delivers the code. A missing email is visible to the caller;
        mask that at the application edge when the product must not reveal registration.
        """
        if purpose not in _PURPOSES:
            raise InvalidIdentityArgument("purpose must be verify_email or login")
        if ttl_seconds <= 0:
            raise InvalidIdentityArgument("ttl_seconds must be positive")
        with self._store.transaction():
            normalized = _normalize_email(email)
            person = self._store.person_by_email(normalized)
            if person is None:
                raise PersonNotFound(normalized)
            if person.status == "frozen":
                raise PersonFrozen(person.id)
            now = self._now()
            code = f"{secrets.randbelow(1_000_000):06d}"
            challenge = VerificationChallenge(
                email=normalized,
                purpose=purpose,
                code_hash=_code_hash(normalized, purpose, code),
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
            self._store.save_challenge(challenge)
            return IssuedCode(
                email=normalized,
                purpose=purpose,
                code=code,
                expires_at=challenge.expires_at,
            )

    def consume_code(self, *, email: str, code: str, purpose: CodePurpose) -> Person:
        if purpose not in _PURPOSES:
            raise InvalidIdentityArgument("purpose must be verify_email or login")
        with self._store.transaction():
            normalized = _normalize_email(email)
            person = self._require_person_by_email(normalized)
            if person.status == "frozen":
                raise PersonFrozen(person.id)
            self._accept_code(normalized, purpose, code)
            if purpose == "verify_email":
                person.email_verified = True
                person.updated_at = self._now()
                validate("person", person.to_dict())
                self._store.save_person(person)
            return person

    def login_with_code(self, *, email: str, code: str, ttl_seconds: int = 86_400) -> Session:
        person = self.consume_code(email=email, code=code, purpose="login")
        return self.open_session(person.id, ttl_seconds=ttl_seconds)

    def freeze_person(self, person_id: str) -> Person:
        return self._set_person_status(person_id, "frozen")

    def unfreeze_person(self, person_id: str) -> Person:
        return self._set_person_status(person_id, "active")

    def open_session(self, person_id: str, *, ttl_seconds: int = 86_400) -> Session:
        if ttl_seconds <= 0:
            raise InvalidIdentityArgument("ttl_seconds must be positive")
        with self._store.transaction():
            person = self._require_person(person_id)
            if person.status == "frozen":
                raise PersonFrozen(person.id)
            now = self._now()
            token = secrets.token_urlsafe(32)
            stored = StoredSession(
                id=_new_id("session"),
                person_id=person.id,
                token_hash=_sha256(token),
                expires_at=now + timedelta(seconds=ttl_seconds),
                created_at=now,
            )
            self._store.save_session(stored)
            return Session(
                id=stored.id,
                person_id=person.id,
                token=token,
                expires_at=stored.expires_at,
                created_at=stored.created_at,
            )

    def require_person(self, token: str) -> Person:
        if not token.strip():
            raise InvalidSession("session is invalid")
        with self._store.transaction():
            stored = self._store.session_by_hash(_sha256(token))
            if stored is None or stored.revoked:
                raise InvalidSession("session is invalid")
            if stored.expires_at <= self._now():
                raise InvalidSession("session is expired")
            person = self._store.get_person(stored.person_id)
            if person is None:
                raise InvalidSession("session person is missing")
            if person.status == "frozen":
                raise PersonFrozen(person.id)
            return person

    def revoke_session(self, token: str) -> None:
        if not token.strip():
            raise InvalidSession("session is invalid")
        with self._store.transaction():
            stored = self._store.session_by_hash(_sha256(token))
            if stored is None:
                raise InvalidSession("session is invalid")
            stored.revoked = True
            self._store.save_session(stored)

    def create_org(self, *, name: str, owner_id: str, org_id: str | None = None) -> Org:
        with self._store.transaction():
            owner = self._require_person(owner_id)
            if owner.status == "frozen":
                raise PersonFrozen(owner.id)
            now = self._now()
            org = Org(
                id=org_id.strip() if org_id else _new_id("org"),
                name=_require_text(name, "name"),
                status="active",
                created_at=now,
                updated_at=now,
            )
            if not org.id:
                raise InvalidIdentityArgument("org_id must be non-empty")
            if self._store.get_org(org.id) is not None:
                raise InvalidIdentityArgument(f"org id already exists: {org.id}")
            validate("org", org.to_dict())
            self._store.save_org(org)
            self._add_member_locked(org, owner, "owner", now)
            return org

    def add_member(self, *, org_id: str, person_id: str, role: MemberRole = "member") -> Membership:
        if role not in _ROLES:
            raise InvalidIdentityArgument("role must be owner, admin, or member")
        with self._store.transaction():
            org = self._require_org(org_id)
            if org.status == "frozen":
                raise OrgFrozen(org.id)
            person = self._require_person(person_id)
            return self._add_member_locked(org, person, role, self._now())

    def freeze_org(self, org_id: str) -> Org:
        return self._set_org_status(org_id, "frozen")

    def unfreeze_org(self, org_id: str) -> Org:
        return self._set_org_status(org_id, "active")

    def list_members(self, org_id: str) -> list[Membership]:
        with self._store.transaction():
            self._require_org(org_id)
            return self._store.memberships_for_org(org_id)

    def link_subject(self, *, connector: str, subject: str, person_id: str) -> ExternalLink:
        connector_name = _require_text(connector, "connector")
        subject_value = _require_text(subject, "subject")
        with self._store.transaction():
            person = self._require_person(person_id)
            existing = self._store.link_for_subject(connector_name, subject_value)
            if existing is not None:
                if existing.person_id != person.id:
                    raise LinkConflict(connector_name)
                return existing
            link = ExternalLink(
                connector=connector_name,
                subject=subject_value,
                person_id=person.id,
                linked_at=self._now(),
            )
            self._store.save_link(link)
            return link

    def resolve_subject(self, *, connector: str, subject: str) -> str | None:
        with self._store.transaction():
            link = self._store.link_for_subject(
                _require_text(connector, "connector"),
                _require_text(subject, "subject"),
            )
            if link is None:
                return None
            return link.person_id

    def login_with_connector(
        self,
        connector: IdentityConnector,
        assertion: Mapping[str, str],
        *,
        ttl_seconds: int = 86_400,
    ) -> Session:
        subject = connector.extract_subject(assertion)
        with self._store.transaction():
            link = self._store.link_for_subject(connector.name, subject)
            if link is None:
                raise UnknownSubject(connector.name)
            person_id = link.person_id
        return self.open_session(person_id, ttl_seconds=ttl_seconds)

    def _add_member_locked(
        self,
        org: Org,
        person: Person,
        role: MemberRole,
        now: datetime,
    ) -> Membership:
        membership = Membership(
            org_id=org.id,
            person_id=person.id,
            role=role,
            created_at=now,
        )
        self._store.save_membership(membership)
        if org.id not in person.org_ids:
            person.org_ids.append(org.id)
            person.updated_at = now
            validate("person", person.to_dict())
            self._store.save_person(person)
        return membership

    def _set_person_status(self, person_id: str, status: Literal["active", "frozen"]) -> Person:
        with self._store.transaction():
            person = self._require_person(person_id)
            person.status = status
            person.updated_at = self._now()
            validate("person", person.to_dict())
            self._store.save_person(person)
            return person

    def _set_org_status(self, org_id: str, status: Literal["active", "frozen"]) -> Org:
        with self._store.transaction():
            org = self._require_org(org_id)
            org.status = status
            org.updated_at = self._now()
            validate("org", org.to_dict())
            self._store.save_org(org)
            return org

    def _accept_code(self, email: str, purpose: CodePurpose, code: str) -> None:
        challenge = self._store.get_challenge(email, purpose)
        if challenge is None or challenge.consumed:
            raise InvalidCode()
        now = self._now()
        if challenge.expires_at <= now or challenge.attempts >= _MAX_CODE_ATTEMPTS:
            raise InvalidCode()
        expected = _code_hash(email, purpose, code.strip())
        if not hmac.compare_digest(challenge.code_hash, expected):
            challenge.attempts += 1
            self._store.save_challenge(challenge)
            raise InvalidCode()
        challenge.consumed = True
        self._store.save_challenge(challenge)

    def _require_person(self, person_id: str) -> Person:
        person = self._store.get_person(_require_text(person_id, "person_id"))
        if person is None:
            raise PersonNotFound(person_id)
        return person

    def _require_person_by_email(self, email: str) -> Person:
        person = self._store.person_by_email(email)
        if person is None:
            raise PersonNotFound(email)
        return person

    def _require_org(self, org_id: str) -> Org:
        org = self._store.get_org(_require_text(org_id, "org_id"))
        if org is None:
            raise OrgNotFound(org_id)
        return org

    def _now(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise InvalidIdentityArgument("clock must return a timezone-aware datetime")
        return current


def _normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        raise InvalidIdentityArgument("email is malformed")
    return normalized


def _require_text(value: str, field: str) -> str:
    text = value.strip()
    if not text:
        raise InvalidIdentityArgument(f"{field} must be non-empty")
    return text


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _code_hash(email: str, purpose: str, code: str) -> str:
    return _sha256(f"{email}\n{purpose}\n{code}")
