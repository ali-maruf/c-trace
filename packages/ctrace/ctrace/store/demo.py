"""Live demo sandboxes: every visitor gets a private copy of the FICTIONAL demo factory.

A sandbox is an organisation flagged `is_demo`, owned by a throwaway demo identity. Demo access tokens are signed
with a key the API generates once and keeps in `app_secrets`; the API accepts them only for demo organisations, so a
demo token can never reach real data. Sandboxes older than SANDBOX_TTL are deleted when the next one is created.
"""
from __future__ import annotations

import time
import uuid

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from . import service
from .db import Conn
from .jobs import run_pending
from .seed import load_demo_factory

DEMO_ISSUER = "c-trace-demo"
SANDBOX_TTL_HOURS = 24
TOKEN_HOURS = 6
KEY_NAME = "demo_jwt_es256_private_pem"


def signing_key(owner_conn: Conn) -> bytes:
    """The demo signing key, created on first use. Concurrent first uses converge on one key (insert … do nothing)."""
    row = owner_conn.execute("select value from app_secrets where name = %s", (KEY_NAME,)).fetchone()
    if row is None:
        pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        owner_conn.execute("insert into app_secrets (name, value) values (%s, %s) on conflict do nothing", (KEY_NAME, pem))
        row = owner_conn.execute("select value from app_secrets where name = %s", (KEY_NAME,)).fetchone()
    assert row is not None
    return str(row["value"]).encode()


def public_key(owner_conn: Conn) -> bytes:
    key = serialization.load_pem_private_key(signing_key(owner_conn), password=None)
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def purge_expired(owner_conn: Conn) -> int:
    rows = owner_conn.execute(
        "delete from organizations where is_demo and created_at < now() - make_interval(hours => %s) returning id",
        (SANDBOX_TTL_HOURS,)).fetchall()
    # only demo identities older than a sandbox's lifetime: a brand-new one from a concurrent start has no membership *yet*
    owner_conn.execute("delete from users u where u.email like 'demo+%%@c-trace.test' "
                       "and u.created_at < now() - make_interval(hours => %s) "
                       "and not exists (select 1 from memberships m where m.user_id = u.id)", (SANDBOX_TTL_HOURS,))
    return len(rows)


def start_sandbox(owner_conn: Conn) -> dict:
    """Owner connection. New sandbox with the demo factory loaded through the normal upload path, plus a token."""
    purge_expired(owner_conn)
    uid = str(uuid.uuid4())
    email = f"demo+{uid[:8]}@c-trace.test"
    service.ensure_user(owner_conn, uid, email, "Demo visitor (fictional)")
    org_id = service.create_organization(owner_conn, "Nodi Knit Composite Ltd. — live demo sandbox (fictional)", uid, is_demo=True)
    fid = load_demo_factory(owner_conn, org_id, uid)
    counts = run_pending(owner_conn, f"demo:{uid[:8]}", org_id=org_id)   # only this sandbox's bills, and wait for all of them
    now = int(time.time())
    token = jwt.encode({"sub": uid, "email": email, "aud": "authenticated", "iss": DEMO_ISSUER, "role": "authenticated",
                        "iat": now, "exp": now + TOKEN_HOURS * 3600}, signing_key(owner_conn), algorithm="ES256")
    return {"token": token, "expires_in": TOKEN_HOURS * 3600, "org_id": org_id, "factory_id": fid, "user_id": uid, "jobs": counts}
