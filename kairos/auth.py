"""Authentication: a bearer credential becomes a ``kairos.access.Caller``.

Two credential types, one result:

- API keys (``kairos_...``). Only the SHA-256 is stored. A key may be bound
  to a person (``person_id``), in which case it can only act as that person.
- SSO JWTs (kairos/sso.py), verified against the issuing tenant's own JWKS.
  The tenant's ``sso_user_claim`` (default ``sub``) names the person.

Authorization (what the caller may read or write) is in kairos/access.py.
"""

from __future__ import annotations

import hashlib
import secrets

from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session as OrmSession

from kairos.access import ROLE_HIERARCHY, Caller
from kairos.db import get_db
from kairos.models import ApiKey, TenantConfig
from kairos.sso import SsoAuthError, resolve_jwt

API_KEY_PREFIX = "kairos_"


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_api_key(
    db: OrmSession, tenant_id: str, label: str | None = None, role: str = "member", person_id: str | None = None
) -> str:
    """Returns the raw key. It is never retrievable again."""
    if role not in ROLE_HIERARCHY:
        raise ValueError(f"unknown role {role!r}")
    raw_key = API_KEY_PREFIX + secrets.token_urlsafe(32)
    db.add(ApiKey(tenant_id=tenant_id, key_hash=hash_key(raw_key), label=label, role=role, person_id=person_id))
    db.flush()
    return raw_key


def caller_from_credential(db: OrmSession, raw_credential: str | None) -> Caller:
    """Raises HTTPException(401) for anything that does not authenticate."""
    if not raw_credential:
        raise HTTPException(status_code=401, detail="missing bearer credential")
    if raw_credential.startswith(API_KEY_PREFIX):
        key_row = db.query(ApiKey).filter(ApiKey.key_hash == hash_key(raw_credential)).first()
        if key_row is None or key_row.revoked_at is not None:
            raise HTTPException(status_code=401, detail="invalid or revoked API key")
        tenant = db.get(TenantConfig, key_row.tenant_id)
        if tenant is None:
            raise HTTPException(status_code=401, detail="invalid or revoked API key")
        caller = Caller(
            tenant_id=tenant.tenant_id,
            org_id=tenant.org_id,
            role=key_row.role,
            person_id=key_row.person_id,
            credential_id=key_row.id,
        )
    else:
        try:
            tenant, role, person_id = resolve_jwt(db, raw_credential)
        except SsoAuthError:
            # Which check failed is not useful to an unauthenticated caller.
            raise HTTPException(status_code=401, detail="SSO authentication failed") from None
        caller = Caller(tenant_id=tenant.tenant_id, org_id=tenant.org_id, role=role, person_id=person_id)

    from kairos.ratelimit import remember_tenant_for_credential

    remember_tenant_for_credential(hash_key(raw_credential), caller.tenant_id)
    return caller


def current_caller(authorization: str | None = Header(default=None), db: OrmSession = Depends(get_db)) -> Caller:
    """Header-only (``Authorization: Bearer ...``), for every JSON endpoint."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing or malformed Authorization header")
    return caller_from_credential(db, authorization.removeprefix("Bearer ").strip())


def caller_via_query_or_header(
    api_key: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
    db: OrmSession = Depends(get_db),
) -> Caller:
    """Header or ``?api_key=``, only for the HTML dashboard, which a browser
    opens by URL. The key ends up in browser history and access logs."""
    raw = (
        authorization.removeprefix("Bearer ").strip()
        if authorization and authorization.startswith("Bearer ")
        else api_key
    )
    return caller_from_credential(db, raw)
