"""Verify the caller's access token and resolve their C-Trace organisation and role.

Production (Supabase Auth): tokens are verified locally against the project's JWKS
(`<SUPABASE_URL>/auth/v1/.well-known/jwks.json`) with ES256/RS256, checking `exp`,
`iss = <SUPABASE_URL>/auth/v1` and `aud = "authenticated"`. Keys are cached for at most 10 minutes,
as Supabase's signing-keys guide advises. Legacy HS256 shared secrets are not accepted.

Development: a local ES256 key pair in `.secrets/` stands in for Supabase (`make dev-token`), accepted
only when CTRACE_ENV=development.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import jwt
from fastapi import Header, HTTPException, Request

from ctrace import config
from ctrace.store import service

DEV_ISSUER = "c-trace-dev"
AUDIENCE = "authenticated"
SECRETS_DIR = config.ROOT / ".secrets"
DEV_PRIVATE_KEY = SECRETS_DIR / "dev_jwt_es256.pem"
DEV_PUBLIC_KEY = SECRETS_DIR / "dev_jwt_es256.pub.pem"
ORG_HEADER = "X-C-Trace-Org"

READ_ROLES = frozenset({"owner", "manager", "viewer", "reviewer", "buyer", "lender"})
RAW_DOCUMENT_ROLES = frozenset({"owner", "manager", "viewer", "reviewer"})  # buyers/lenders: no raw bills
MANAGE_ROLES = frozenset({"owner", "manager"})
REVIEW_ROLES = frozenset({"owner", "manager", "reviewer"})


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str | None
    org_id: str
    role: str

    def require(self, roles: frozenset[str], action: str) -> None:
        if self.role not in roles:
            raise HTTPException(403, f"Your role ({self.role}) cannot {action}.")


def supabase_url() -> str | None:
    url = os.getenv("SUPABASE_URL")
    return url.rstrip("/") if url else None


@lru_cache(maxsize=1)
def _jwks_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{supabase_url()}/auth/v1/.well-known/jwks.json", cache_keys=True, lifespan=600)


@lru_cache(maxsize=4)
def _dev_public_key(path: Path) -> bytes:
    return path.read_bytes()


def dev_public_key_path() -> Path:
    return Path(os.getenv("CTRACE_DEV_JWT_PUBLIC_KEY", str(DEV_PUBLIC_KEY)))


def verify_token(token: str) -> dict:
    try:
        issuer = jwt.decode(token, options={"verify_signature": False}).get("iss")
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Malformed access token") from exc
    sb = supabase_url()
    try:
        if sb and issuer == f"{sb}/auth/v1":
            key = _jwks_client().get_signing_key_from_jwt(token).key
            return jwt.decode(token, key, algorithms=["ES256", "RS256"], audience=AUDIENCE, issuer=issuer,
                              options={"require": ["exp", "sub", "iss", "aud"]})
        if issuer == DEV_ISSUER and os.getenv("CTRACE_ENV", "development") == "development":
            path = dev_public_key_path()
            if not path.exists():
                raise HTTPException(401, "Development key missing; run `make dev-token`")
            return jwt.decode(token, _dev_public_key(path), algorithms=["ES256"], audience=AUDIENCE, issuer=DEV_ISSUER,
                              options={"require": ["exp", "sub", "iss", "aud"]})
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"Invalid access token ({type(exc).__name__})") from exc
    raise HTTPException(401, "Access token issuer is not accepted by this C-Trace deployment")


def current_principal(request: Request, authorization: str | None = Header(default=None),
                      x_c_trace_org: str | None = Header(default=None, alias=ORG_HEADER)) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    claims = verify_token(authorization.split(" ", 1)[1].strip())
    user_id = str(claims["sub"])
    pool = request.app.state.pool
    with pool.connection() as conn:
        memberships = service.memberships_for(conn, user_id)
    if not memberships:
        raise HTTPException(403, "This account has no C-Trace organisation yet")
    if x_c_trace_org:
        chosen = next((m for m in memberships if m["org_id"] == x_c_trace_org), None)
        if chosen is None:
            raise HTTPException(403, "You are not a member of that organisation")
    elif len(memberships) == 1:
        chosen = memberships[0]
    else:
        raise HTTPException(400, f"You belong to several organisations; send the {ORG_HEADER} header")
    return Principal(user_id=user_id, email=claims.get("email"), org_id=chosen["org_id"], role=chosen["role"])


def platform_admin_emails() -> set[str]:
    return {e.strip().lower() for e in os.getenv("CTRACE_PLATFORM_ADMIN_EMAILS", "").split(",") if e.strip()}


def require_platform_admin(p: Principal) -> None:
    """Reference data (emission factors) is global, so only C-Trace platform admins may enter it."""
    if not p.email or p.email.lower() not in platform_admin_emails():
        raise HTTPException(403, "Only C-Trace platform admins can enter official emission factors.")
