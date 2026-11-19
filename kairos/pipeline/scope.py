"""Scope resolution: which scopes may this caller read for this request?

Fails closed. Every scope in the result is derived from the directory and
checked against the caller, never taken from the request on trust:

- The person is the credential's person (kairos.access.acting_person_id).
- The team is the person's own team.
- A project (from the session, or from ``repo``) is included only if the
  person's team owns it. Another team's repo is left out, with a note.
- A session is included only if it belongs to the person. Naming someone
  else's session is refused (AccessDenied). An unknown session id is
  ignored, with a note, since agents often send session ids Kairos has
  never seen.
- A caller acting as nobody (an unbound non-admin credential, or a user id
  not in the directory) gets the org layer only.
"""

from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from kairos.access import AccessDenied, Caller, acting_person_id, person_in_org
from kairos.directory import Org, Project, SessionRecord, project_for_repo
from kairos.pipeline.types import ContextPrincipal, ResolvedScope, ScopeRef


class DirectoryScopeResolver:
    def resolve(self, db: OrmSession, caller: Caller, principal: ContextPrincipal) -> ResolvedScope:
        notes: list[str] = []
        if db.get(Org, caller.org_id) is None:
            raise AccessDenied("tenant org is not in the directory")
        chain = [ScopeRef("org", caller.org_id)]

        person_id = acting_person_id(caller, principal.user_id)
        if principal.user_id and person_id is None:
            notes.append("credential is not bound to a user: only the org layer is available")
        person = person_in_org(db, person_id, caller.org_id) if person_id else None
        if person_id and person is None:
            notes.append(f"unknown user '{person_id}': only the org layer is available")

        session = None
        if principal.session_id:
            session = db.get(SessionRecord, principal.session_id)
            if session is None:
                notes.append(f"unknown session '{principal.session_id}' ignored")
            elif person is None or session.person_id != person.id:
                raise AccessDenied("session belongs to another user")

        project = None
        if session is not None:
            project = db.get(Project, session.project_id)
        elif principal.repo:
            project = project_for_repo(db, caller.org_id, principal.repo)
            if project is None:
                notes.append(f"unknown repo '{principal.repo}' ignored")

        if person is not None:
            chain.append(ScopeRef("team", person.team_id))
            if project is not None:
                if project.team_id == person.team_id:
                    chain.append(ScopeRef("project", project.id))
                else:
                    notes.append(f"project '{project.id}' belongs to another team: not included")
            chain.append(ScopeRef("personal", person.id))
            if session is not None:
                chain.append(ScopeRef("session", session.id))
        elif project is not None:
            notes.append(f"project '{project.id}' needs a known user on its team: not included")

        return ResolvedScope(
            tenant_id=caller.tenant_id,
            org_id=caller.org_id,
            person_id=person.id if person else None,
            chain=tuple(chain),
            notes=tuple(notes),
        )
