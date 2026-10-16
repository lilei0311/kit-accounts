from datetime import UTC, datetime, timedelta

import pytest

from maxkit_contracts import validate
from maxkit_identity import (
    AssertionConnector,
    EmailTaken,
    IdentityService,
    InvalidAssertion,
    InvalidCode,
    InvalidSession,
    LinkConflict,
    OrgFrozen,
    PersonFrozen,
    PersonNotFound,
    UnknownSubject,
)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 16, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def test_import_surface():
    import maxkit_identity

    assert maxkit_identity.IdentityService is IdentityService


def test_register_validates_person_and_rejects_duplicate_email():
    service = IdentityService(clock=Clock())
    person = service.register_person(
        display_name="Ada",
        email="Ada@Example.com",
        person_id="person_ada",
    )
    validate("person", person.to_dict())
    assert person.email == "ada@example.com"
    assert person.email_verified is False
    with pytest.raises(EmailTaken):
        service.register_person(display_name="Other", email="ada@example.com")


def test_verification_code_and_login_session():
    clock = Clock()
    service = IdentityService(clock=clock)
    service.register_person(display_name="Ada", email="ada@example.com", person_id="p1")
    issued = service.issue_code(email="ada@example.com", purpose="verify_email", ttl_seconds=60)
    assert issued.code.isdigit()
    verified = service.consume_code(
        email="ada@example.com",
        code=issued.code,
        purpose="verify_email",
    )
    assert verified.email_verified is True
    with pytest.raises(InvalidCode):
        service.consume_code(email="ada@example.com", code=issued.code, purpose="verify_email")

    login = service.issue_code(email="ada@example.com", purpose="login")
    session = service.login_with_code(email="ada@example.com", code=login.code)
    assert service.require_person(session.token).id == "p1"
    service.revoke_session(session.token)
    with pytest.raises(InvalidSession):
        service.require_person(session.token)


def test_wrong_code_locks_after_five_attempts_and_expiry():
    clock = Clock()
    service = IdentityService(clock=clock)
    service.register_person(display_name="Ada", email="ada@example.com", person_id="p1")
    issued = service.issue_code(email="ada@example.com", purpose="login", ttl_seconds=30)
    for _ in range(5):
        with pytest.raises(InvalidCode):
            service.consume_code(email="ada@example.com", code="000000", purpose="login")
    with pytest.raises(InvalidCode):
        service.consume_code(email="ada@example.com", code=issued.code, purpose="login")

    fresh = service.issue_code(email="ada@example.com", purpose="login", ttl_seconds=30)
    clock.now += timedelta(seconds=31)
    with pytest.raises(InvalidCode):
        service.consume_code(email="ada@example.com", code=fresh.code, purpose="login")


def test_frozen_person_blocks_session_and_does_not_burn_code():
    clock = Clock()
    service = IdentityService(clock=clock)
    service.register_person(display_name="Ada", email="ada@example.com", person_id="p1")
    session = service.open_session("p1", ttl_seconds=100)
    issued = service.issue_code(email="ada@example.com", purpose="login")
    service.freeze_person("p1")
    with pytest.raises(PersonFrozen):
        service.require_person(session.token)
    with pytest.raises(PersonFrozen):
        service.open_session("p1")
    with pytest.raises(PersonFrozen):
        service.issue_code(email="ada@example.com", purpose="login")
    with pytest.raises(PersonFrozen):
        service.consume_code(email="ada@example.com", code=issued.code, purpose="login")
    service.unfreeze_person("p1")
    assert (
        service.consume_code(email="ada@example.com", code=issued.code, purpose="login").id == "p1"
    )


def test_session_expiry():
    clock = Clock()
    service = IdentityService(clock=clock)
    service.register_person(display_name="Ada", email="ada@example.com", person_id="p1")
    session = service.open_session("p1", ttl_seconds=10)
    clock.now += timedelta(seconds=11)
    with pytest.raises(InvalidSession):
        service.require_person(session.token)


def test_org_membership_and_freeze():
    service = IdentityService(clock=Clock())
    owner = service.register_person(display_name="Ada", email="ada@example.com", person_id="p1")
    member = service.register_person(display_name="Bea", email="bea@example.com", person_id="p2")
    org = service.create_org(name="Example Studio", owner_id=owner.id, org_id="org1")
    validate("org", org.to_dict())
    service.add_member(org_id=org.id, person_id=member.id, role="member")
    assert [row.person_id for row in service.list_members(org.id)] == ["p1", "p2"]
    assert "org1" in service.require_person(service.open_session("p2").token).org_ids
    with pytest.raises(PersonNotFound):
        service.add_member(org_id=org.id, person_id="missing")
    service.freeze_org(org.id)
    with pytest.raises(OrgFrozen):
        service.add_member(org_id=org.id, person_id=owner.id, role="admin")


def test_connector_links_subject_to_person_then_session():
    service = IdentityService(clock=Clock())
    service.register_person(display_name="Ada", person_id="p1")
    service.link_subject(connector="wechat", subject="openid-1", person_id="p1")
    again = service.link_subject(connector="wechat", subject="openid-1", person_id="p1")
    assert again.person_id == "p1"
    other = service.register_person(display_name="Bea", person_id="p2")
    with pytest.raises(LinkConflict):
        service.link_subject(connector="wechat", subject="openid-1", person_id=other.id)
    connector = AssertionConnector("wechat")
    with pytest.raises(UnknownSubject):
        service.login_with_connector(connector, {"subject": "missing"})
    with pytest.raises(InvalidAssertion):
        service.login_with_connector(connector, {"subject": "  "})
    session = service.login_with_connector(connector, {"subject": "openid-1"})
    assert service.require_person(session.token).id == "p1"
    assert service.resolve_subject(connector="wechat", subject="openid-1") == "p1"
    service.freeze_person("p1")
    with pytest.raises(PersonFrozen):
        service.login_with_connector(connector, {"subject": "openid-1"})
