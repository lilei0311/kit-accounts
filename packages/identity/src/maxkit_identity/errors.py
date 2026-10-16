"""Identity failures. Messages name the account id, never a code or token."""


class IdentityError(Exception):
    """Base error for identity operations."""


class InvalidIdentityArgument(IdentityError):
    """Caller passed an empty id, email, or other malformed argument."""


class EmailTaken(IdentityError):
    def __init__(self, email: str) -> None:
        super().__init__(f"email already registered: {email}")
        self.email = email


class PersonNotFound(IdentityError):
    def __init__(self, person_id: str) -> None:
        super().__init__(f"person not found: {person_id}")
        self.person_id = person_id


class OrgNotFound(IdentityError):
    def __init__(self, org_id: str) -> None:
        super().__init__(f"org not found: {org_id}")
        self.org_id = org_id


class PersonFrozen(IdentityError):
    def __init__(self, person_id: str) -> None:
        super().__init__(f"person is frozen: {person_id}")
        self.person_id = person_id


class OrgFrozen(IdentityError):
    def __init__(self, org_id: str) -> None:
        super().__init__(f"org is frozen: {org_id}")
        self.org_id = org_id


class InvalidSession(IdentityError):
    def __init__(self, message: str = "session is invalid") -> None:
        super().__init__(message)


class InvalidCode(IdentityError):
    def __init__(self, message: str = "verification code is invalid") -> None:
        super().__init__(message)


class InvalidAssertion(IdentityError):
    """An identity connector could not read a subject from the assertion."""


class UnknownSubject(IdentityError):
    def __init__(self, connector: str) -> None:
        super().__init__(f"no person linked for connector {connector}")
        self.connector = connector


class LinkConflict(IdentityError):
    def __init__(self, connector: str) -> None:
        super().__init__(f"connector subject already linked: {connector}")
        self.connector = connector
