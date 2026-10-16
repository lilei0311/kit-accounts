"""In-memory identity store. A later SQL adapter can implement the same methods."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock
from typing import Protocol

from maxkit_identity.models import (
    ExternalLink,
    Membership,
    Org,
    Person,
    StoredSession,
    VerificationChallenge,
)


class IdentityStore(Protocol):
    def transaction(self) -> Iterator[None]: ...

    def save_person(self, person: Person) -> None: ...

    def get_person(self, person_id: str) -> Person | None: ...

    def person_by_email(self, email: str) -> Person | None: ...

    def save_org(self, org: Org) -> None: ...

    def get_org(self, org_id: str) -> Org | None: ...

    def save_membership(self, membership: Membership) -> None: ...

    def memberships_for_org(self, org_id: str) -> list[Membership]: ...

    def save_challenge(self, challenge: VerificationChallenge) -> None: ...

    def get_challenge(self, email: str, purpose: str) -> VerificationChallenge | None: ...

    def save_session(self, session: StoredSession) -> None: ...

    def session_by_hash(self, token_hash: str) -> StoredSession | None: ...

    def save_link(self, link: ExternalLink) -> None: ...

    def link_for_subject(self, connector: str, subject: str) -> ExternalLink | None: ...


class MemoryIdentityStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self.persons: dict[str, Person] = {}
        self.emails: dict[str, str] = {}
        self.orgs: dict[str, Org] = {}
        self.memberships: dict[tuple[str, str], Membership] = {}
        self.challenges: dict[tuple[str, str], VerificationChallenge] = {}
        self.sessions: dict[str, StoredSession] = {}
        self.links: dict[tuple[str, str], ExternalLink] = {}

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            yield

    def save_person(self, person: Person) -> None:
        previous = self.persons.get(person.id)
        if previous is not None and previous.email and previous.email != person.email:
            self.emails.pop(previous.email, None)
        self.persons[person.id] = person
        if person.email:
            self.emails[person.email] = person.id

    def get_person(self, person_id: str) -> Person | None:
        return self.persons.get(person_id)

    def person_by_email(self, email: str) -> Person | None:
        person_id = self.emails.get(email)
        if person_id is None:
            return None
        return self.persons.get(person_id)

    def save_org(self, org: Org) -> None:
        self.orgs[org.id] = org

    def get_org(self, org_id: str) -> Org | None:
        return self.orgs.get(org_id)

    def save_membership(self, membership: Membership) -> None:
        self.memberships[(membership.org_id, membership.person_id)] = membership

    def memberships_for_org(self, org_id: str) -> list[Membership]:
        rows = [row for row in self.memberships.values() if row.org_id == org_id]
        return sorted(rows, key=lambda row: (row.created_at, row.person_id))

    def save_challenge(self, challenge: VerificationChallenge) -> None:
        self.challenges[(challenge.email, challenge.purpose)] = challenge

    def get_challenge(self, email: str, purpose: str) -> VerificationChallenge | None:
        return self.challenges.get((email, purpose))

    def save_session(self, session: StoredSession) -> None:
        self.sessions[session.token_hash] = session

    def session_by_hash(self, token_hash: str) -> StoredSession | None:
        return self.sessions.get(token_hash)

    def save_link(self, link: ExternalLink) -> None:
        self.links[(link.connector, link.subject)] = link

    def link_for_subject(self, connector: str, subject: str) -> ExternalLink | None:
        return self.links.get((connector, subject))
