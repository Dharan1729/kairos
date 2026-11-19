"""Per-tenant rate limiting (slowapi/limits). Protects the service from a
single noisy caller — buggy retry loop, runaway agent, or a compromised
key — starving every other tenant sharing the same process.

slowapi's key_func only ever sees the raw ASGI Request, not the resolved
AuthContext (that resolution happens later, inside a FastAPI dependency,
after the rate-limit check has already run). Resolving the tenant_id a
second time here via a DB query on every request would defeat the point of
a fast pre-request check, so kairos/auth.py writes into
_credential_to_tenant, a small in-process cache, right after it resolves a
credential — this module reads that cache to bucket by tenant_id when it
can. A request from a not-yet-cached credential falls back to bucketing by
remote IP (kept, not a per-credential "cold" bucket — see below) until its
tenant is known, then joins that tenant's real bucket from then on.

An earlier version of this fallback bucketed by a hash of the credential
itself instead of by IP. That worked fine for API keys (long-lived —
"not yet cached" only ever happens once, ever, per key) but broke down for
kairos/sso.py's JWTs, which commonly rotate every login/refresh: every
reissued token is a brand-new "unseen" credential, so it hashed to a
brand-new cold bucket on every single refresh — an SSO tenant's real limit
was never actually enforced, not just leniently applied once. Bucketing
the not-yet-resolved case by IP instead closes that: IP doesn't rotate
with the token, so repeated refreshes from the same caller keep landing in
the same (IP) bucket rather than each getting its own fresh allowance.
"""

import os
from collections import OrderedDict

from fastapi import Request
from slowapi import Limiter
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

DEFAULT_RATE_LIMIT = os.environ.get("KAIROS_RATE_LIMIT", "120/minute")
_MAX_CACHED_CREDENTIALS = 4096

_credential_to_tenant: "OrderedDict[str, str]" = OrderedDict()


def remember_tenant_for_credential(credential_hash: str, tenant_id: str) -> None:
    """Called by kairos/auth.py after successfully resolving a bearer
    credential (API key or SSO JWT), so subsequent requests from the same
    credential rate-limit against the right tenant bucket without a DB
    round trip. Same bounded-LRU shape as vector_index.py's index cache."""
    _credential_to_tenant[credential_hash] = tenant_id
    _credential_to_tenant.move_to_end(credential_hash)
    if len(_credential_to_tenant) > _MAX_CACHED_CREDENTIALS:
        _credential_to_tenant.popitem(last=False)


def _tenant_rate_limit_key(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return get_remote_address(request)
    raw_credential = auth_header.removeprefix("Bearer ").strip()
    # Local import avoids a module import cycle: auth.py needs to call
    # remember_tenant_for_credential() (defined above) after resolving a
    # credential, so this module can't import hash_key from auth.py at
    # module scope without auth.py <-> ratelimit.py becoming circular.
    from kairos.auth import hash_key

    credential_hash = hash_key(raw_credential)
    tenant_id = _credential_to_tenant.get(credential_hash)
    # IP fallback, not a per-credential bucket — see module docstring for
    # why: a per-credential fallback bucket gives short-lived credentials
    # (SSO JWTs, which rotate on every refresh) a fresh, never-metered
    # bucket on every single request, defeating the limit entirely rather
    # than leniently applying it once.
    return tenant_id if tenant_id else get_remote_address(request)


# swallow_errors=True: the same fail-open philosophy applied everywhere
# else non-critical paths can fail (graph centrality, ANN vector search) —
# a bug or backend hiccup in rate limiting should degrade to "unlimited for
# this request," not 500 every caller until it's fixed.
limiter = Limiter(key_func=_tenant_rate_limit_key, default_limits=[DEFAULT_RATE_LIMIT], swallow_errors=True)


def install_rate_limiting(app) -> None:
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)


def reset_rate_limiter_for_tests() -> None:
    limiter.reset()
    _credential_to_tenant.clear()
