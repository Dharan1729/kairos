"""SSO: validates a JWT bearer token against a per-tenant identity
provider's JWKS, as a second authentication path alongside the API keys in
kairos/auth.py.

Multi-tenant SSO can't use one global issuer/JWKS — each tenant brings its
own IdP (Okta, Azure AD, Google Workspace, ...), so
tenant resolution has to happen from the token itself before it can even
be verified: read the (unverified) `iss` claim, look up which tenant
configured that issuer (TenantConfig.sso_issuer, kairos/models.py), then
verify the token's signature against THAT tenant's JWKS before trusting
anything else in it. Getting this order backwards — trusting any claim
before the signature is checked — is the standard JWT-multi-tenancy
pitfall this module exists to avoid.

TenantConfig.sso_jwks_static (a pinned JWKS JSON blob) is checked before
sso_jwks_url (a live fetch) — this is what makes SSO testable without a
running IdP, and doubles as a legitimate production option for pinning a
known-good JWKS rather than trusting a live endpoint on every cold cache.
"""

import json
import time

import httpx
import jwt

from kairos.access import ROLE_HIERARCHY
from kairos.models import TenantConfig

_JWKS_CACHE_TTL_SECONDS = 3600
_jwks_cache: dict[str, tuple[float, dict]] = {}


class SsoAuthError(Exception):
    """Any failure in JWT/JWKS resolution — kairos/auth.py turns this into
    a 401, same treatment as an invalid API key. Deliberately one error
    type: which specific check failed (bad signature vs. unknown issuer vs.
    expired) is not information worth handing back to an unauthenticated
    caller."""


def _fetch_jwks(jwks_url: str) -> dict:
    now = time.time()
    cached = _jwks_cache.get(jwks_url)
    if cached and now - cached[0] < _JWKS_CACHE_TTL_SECONDS:
        return cached[1]
    try:
        resp = httpx.get(jwks_url, timeout=5.0)
        resp.raise_for_status()
        jwks = resp.json()
    except Exception as e:
        raise SsoAuthError(f"failed to fetch JWKS from {jwks_url}: {e}") from e
    _jwks_cache[jwks_url] = (now, jwks)
    return jwks


def _signing_key_for(jwks: dict, kid: str | None):
    """A token with no `kid` in its header matches the first key in the
    set — correct for a single-key JWKS (the common case, and what's
    testable without a real IdP), ambiguous during an IdP's key-rotation
    window with multiple published keys and a kid-less token. Not a
    signature-verification bypass either way: picking the wrong key just
    makes the subsequent jwt.decode() signature check fail closed (401),
    never succeed on the wrong key."""
    for key_dict in jwks.get("keys", []):
        if kid is None or key_dict.get("kid") == kid:
            return jwt.PyJWK.from_dict(key_dict).key
    return None


def resolve_jwt(db, token: str) -> tuple[TenantConfig, str, str | None]:
    """Verifies `token` and returns (tenant, role, person_id). person_id comes
    from the tenant's sso_user_claim (default "sub"); it binds the token to
    that directory person exactly like a person-bound API key. Raises
    SsoAuthError on
    any failure — malformed token, unrecognized issuer, bad signature,
    expired, wrong audience, or no matching JWKS key."""
    try:
        unverified_claims = jwt.decode(token, options={"verify_signature": False})
        header = jwt.get_unverified_header(token)
    except jwt.exceptions.PyJWTError as e:
        raise SsoAuthError(f"malformed bearer token: {e}") from e

    issuer = unverified_claims.get("iss")
    if not issuer:
        raise SsoAuthError("token has no 'iss' claim")

    tenant = db.query(TenantConfig).filter(TenantConfig.sso_issuer == issuer).first()
    if tenant is None or not (tenant.sso_jwks_static or tenant.sso_jwks_url):
        raise SsoAuthError(f"no tenant has SSO configured for issuer {issuer!r}")

    jwks = json.loads(tenant.sso_jwks_static) if tenant.sso_jwks_static else _fetch_jwks(tenant.sso_jwks_url)
    signing_key = _signing_key_for(jwks, header.get("kid"))
    if signing_key is None:
        raise SsoAuthError(f"no JWKS key matches kid={header.get('kid')!r} for issuer {issuer!r}")

    # algorithms is a fixed allow-list (never derived from the token's own
    # header) — the standard defense against "alg: none" / RS256-to-HS256
    # confusion attacks, where a token claims a weaker/no-signature
    # algorithm and an implementation that trusted the header would accept
    # it unverified or verify it against the wrong kind of key.
    #
    # If sso_audience is unset for a tenant, PyJWT skips audience
    # verification only when the token also carries no `aud` claim; a
    # token that does include one still gets its audience checked against
    # nothing and is rejected (fails closed). An admin who wants Kairos to
    # accept any audience from their trusted issuer needs to know that's
    # the effect of leaving sso_audience empty — same tradeoff as omitting
    # `audience` from any OIDC relying-party config.
    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256", "ES256"],
            audience=tenant.sso_audience,
            issuer=tenant.sso_issuer,
            options={"require": ["exp"]},
        )
    except jwt.exceptions.PyJWTError as e:
        raise SsoAuthError(f"token validation failed: {e}") from e

    role = claims.get(tenant.sso_role_claim)
    if role not in ROLE_HIERARCHY:
        role = tenant.sso_default_role
    user_claim = tenant.sso_user_claim or "sub"
    person_id = claims.get(user_claim)
    return tenant, role, str(person_id) if person_id else None


def reset_jwks_cache_for_tests() -> None:
    _jwks_cache.clear()
