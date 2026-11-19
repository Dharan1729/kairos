"""The org directory: orgs, teams, people, projects and sessions.

This is the data scope resolution walks. Kairos does not manage it through
the API; it is loaded by ``kairos seed`` (or by your own sync job) from the
system of record for who works where.

``describe_scope`` answers, for any scope id, what kind of scope it is and
who owns it. Every authorization decision on a scope id goes through it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import Session as OrmSession

from kairos.db import Base


class Org(Base):
    __tablename__ = "orgs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    policies: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON list[str]
    standards: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON list[str]


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    org_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)


class Person(Base):
    __tablename__ = "people"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str | None] = mapped_column(String, nullable=True)
    team_id: Mapped[str] = mapped_column(String, nullable=False)
    manager_id: Mapped[str | None] = mapped_column(String, nullable=True)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    team_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    repo_name: Mapped[str] = mapped_column(String, nullable=False, index=True)


class SessionRecord(Base):
    """A known agent session. Named to avoid clashing with SQLAlchemy's Session."""

    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    person_id: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    task_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[str | None] = mapped_column(String, nullable=True)
    recent_messages: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON list[str]


# The layer an item in each kind of scope belongs to.
LAYER_FOR_KIND = {"org": "org", "team": "team", "project": "project", "person": "personal", "session": "session"}
SHARED_LAYERS = frozenset({"org", "team", "project"})
PRIVATE_LAYERS = frozenset({"personal", "session"})


@dataclass(frozen=True)
class ScopeInfo:
    """What a scope id refers to. ``team_id`` is the owning team for team and
    project scopes and the person's team for person and session scopes.
    ``person_id`` is set only for person and session scopes."""

    kind: str  # org | team | project | person | session
    scope_id: str
    org_id: str | None
    team_id: str | None = None
    person_id: str | None = None

    @property
    def layer(self) -> str:
        return LAYER_FOR_KIND[self.kind]


def _team_org(db: OrmSession, team_id: str | None) -> str | None:
    team = db.get(Team, team_id) if team_id else None
    return team.org_id if team else None


def describe_scope(db: OrmSession, scope_id: str | None) -> ScopeInfo | None:
    """Returns None for an id that matches nothing. Callers treat that as
    "not yours" (fail closed): there is no API to create scopes, so an id
    that resolves to nothing is never a legitimate new scope."""
    if not scope_id:
        return None
    if db.get(Org, scope_id):
        return ScopeInfo("org", scope_id, org_id=scope_id)
    team = db.get(Team, scope_id)
    if team:
        return ScopeInfo("team", scope_id, org_id=team.org_id, team_id=team.id)
    project = db.get(Project, scope_id)
    if project:
        return ScopeInfo("project", scope_id, org_id=_team_org(db, project.team_id), team_id=project.team_id)
    person = db.get(Person, scope_id)
    if person:
        return ScopeInfo(
            "person", scope_id, org_id=_team_org(db, person.team_id), team_id=person.team_id, person_id=person.id
        )
    session = db.get(SessionRecord, scope_id)
    if session:
        owner = db.get(Person, session.person_id)
        team_id = owner.team_id if owner else None
        return ScopeInfo(
            "session", scope_id, org_id=_team_org(db, team_id), team_id=team_id, person_id=session.person_id
        )
    return None


def project_for_repo(db: OrmSession, org_id: str, repo: str) -> Project | None:
    """Repo names are only unique within an org, so the lookup is limited
    to the caller's org."""
    return (
        db.query(Project)
        .join(Team, Team.id == Project.team_id)
        .filter(Team.org_id == org_id, Project.repo_name == repo)
        .first()
    )


def load_json_field(value: str | None) -> list:
    return json.loads(value) if value else []
