"""Development-only access tokens.   python -m ctrace_api.devtoken [--user-id ID] [--email E] [--hours 12]

Generates a local ES256 key pair in `.secrets/` (git-ignored) on first use and prints a token for the
fictional demo user, so the API can be exercised without a Supabase project. The API accepts these
tokens only when CTRACE_ENV=development.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from ctrace.store.seed import DEMO_EMAIL, DEMO_USER_ID

from .auth import AUDIENCE, DEV_ISSUER, DEV_PRIVATE_KEY, DEV_PUBLIC_KEY


def ensure_keys(private_path: Path = DEV_PRIVATE_KEY, public_path: Path = DEV_PUBLIC_KEY) -> bytes:
    if not private_path.exists():
        private_path.parent.mkdir(parents=True, exist_ok=True)
        key = ec.generate_private_key(ec.SECP256R1())
        private_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()))
        private_path.chmod(0o600)
        public_path.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                                                              serialization.PublicFormat.SubjectPublicKeyInfo))
    return private_path.read_bytes()


def make_token(user_id: str, email: str, hours: float = 12, private_key: bytes | None = None) -> str:
    now = int(time.time())
    claims = {"sub": user_id, "email": email, "aud": AUDIENCE, "iss": DEV_ISSUER, "role": "authenticated",
              "iat": now, "exp": now + int(hours * 3600)}
    return jwt.encode(claims, private_key or ensure_keys(), algorithm="ES256")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user-id", default=DEMO_USER_ID)
    ap.add_argument("--email", default=DEMO_EMAIL)
    ap.add_argument("--hours", type=float, default=12)
    a = ap.parse_args()
    print(make_token(a.user_id, a.email, a.hours))


if __name__ == "__main__":
    main()
