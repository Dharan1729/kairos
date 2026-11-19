"""Who is asking, and what they may read and write.

Every adapter (HTTP API, MCP server through the API, CLI) turns its
credential into a ``Caller`` and passes it to the core. The core never
trusts identity fields in a request body on their own: a request can only
act as the person its credential is bound to, unless the credential is an
admin service credential (see docs/guide/concepts.md).

Errors are plain exceptions; adapters map them to status codes:
``AccessDenied`` -> 403, ``NotFound`` -> 404, ``InvalidRequest`` -> 422.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session as OrmSession

from kairos.directory import Person, ScopeInfo, Team, describe_scope

Role = Literal["admin", "member", "viewer"]
ROLE_HIERARCHY: dict[str, int] = {"viewer": 0, "member": 1, "admin": 2}


class AccessDenied(Exception):
    pass


class NotFound(Exception):
    pass


class InvalidRequest(Exception):
    pass


@dataclass(frozen=True)
class Caller:
    """An authenticated credential.

    person_id: the directory person the credential is bound to, or None for
    a service credential. credential_id: the API key row id (None for SSO)."""

    tenant_id: str
    org_id: str
    role: str
    person_id: str | None = None
    credential_id: int | None = None

    def has_role(self, minimum: str) -> bool:
        return ROLE_HIERARCHY.get(self.role, -1) >= ROLE_HIERARCHY[minimum]

    @property
    def is_admin(self) -> bool:
        return self.has_role("admin")


def require_role(caller: Caller, minimum: str) -> None:
    if not caller.has_role(minimum):
        raise AccessDenied(f"this action requires the '{minimum}' role or higher; this credential has '{caller.role}'")


def acting_person_id(caller: Caller, requested_user_id: str | None) -> str | None:
    """Which person a request acts as.

    - A credential bound to a person acts as that person. Naming anyone else
      is refused, never silently corrected.
    - An unbound admin credential (a trusted gateway that authenticates its
      own users) may act as any person in its tenant.
    - Any other unbound credential acts as nobody: it can read the org layer
      only. Returns None in that case.
    """
    if caller.person_id:
        if requested_user_id and requested_user_id != caller.person_id:
            raise AccessDenied("this credential is bound to a different user")
        return caller.person_id
    if caller.is_admin:
        return requested_user_id or None
    return None


def person_in_org(db: OrmSession, person_id: str, org_id: str) -> Person | None:
    """The person, if they exist and belong to org_id. A person in another
    org is reported as AccessDenied, not as missing."""
    person = db.get(Person, person_id)
    if person is None:
        return None
    team = db.get(Team, person.team_id)
    if team is None or team.org_id != org_id:
        raise AccessDenied("user does not belong to this tenant")
    return person


def scope_in_tenant(db: OrmSession, caller: Caller, scope_id: str | None) -> ScopeInfo:
    """Fails closed: an unknown scope id is treated exactly like another
    tenant's scope."""
    info = describe_scope(db, scope_id)
    if info is None or info.org_id is None or info.org_id != caller.org_id:
        raise AccessDenied("scope does not belong to this tenant")
    return info


def check_can_write(db: OrmSession, caller: Caller, layer: str, scope_id: str) -> ScopeInfo:
    """May ``caller`` add knowledge (or propose it) in ``scope_id``?

    - viewer: never.
    - admin: any scope in the tenant.
    - member bound to a person: their team's team and project scopes, their
      own personal scope and their own sessions. Org-wide facts need an admin.
    - member service credential (unbound): team and project scopes only
      (ingestion bots); never personal, session or org.

    The layer must match the kind of scope (a 'personal' item must live in
    a person scope, and so on), so a private item can never be filed under a
    shared scope.
    """
    require_role(caller, "member")
    info = scope_in_tenant(db, caller, scope_id)
    if info.layer != layer:
        raise InvalidRequest(
            f"layer '{layer}' does not match scope '{scope_id}' (a {info.kind} scope needs layer '{info.layer}')"
        )
    if caller.is_admin:
        return info
    if layer == "org":
        raise AccessDenied("org-layer knowledge can only be written by an admin")
    if layer in ("team", "project"):
        if caller.person_id is None:
            return info
        me = db.get(Person, caller.person_id)
        if me is not None and me.team_id == info.team_id:
            return info
        raise AccessDenied("you can only write to your own team's scopes")
    # personal / session
    if caller.person_id is not None and caller.person_id == info.person_id:
        return info
    raise AccessDenied("personal and session knowledge can only be written by its owner")


def can_write(db: OrmSession, caller: Caller, layer: str, scope_id: str) -> bool:
    try:
        check_can_write(db, caller, layer, scope_id)
        return True
    except (AccessDenied, InvalidRequest):
        return False
