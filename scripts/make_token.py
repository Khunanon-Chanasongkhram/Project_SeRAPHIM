#!/usr/bin/env python3
"""Mint a responder access token for the SOS ops console.

The raw token is shown ONCE and never stored anywhere — only its SHA-256 goes in the
database, so a leaked backup cannot be used to sign in. Hand the token to the person
over a channel you trust, and record who has which id.

    python3 scripts/make_token.py --role official --name "Somchai" --org "Bang Phli PAO"
    python3 scripts/make_token.py --role volunteer --scope "สมุทรปราการ"
"""

from __future__ import annotations

import argparse
import hashlib
import secrets
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", choices=["volunteer", "official", "admin"], default="official")
    ap.add_argument("--name", default="")
    ap.add_argument("--org", default="")
    ap.add_argument("--scope", default="", help="restrict to one province (recommended)")
    ap.add_argument("--days", type=int, default=365, help="expiry in days (0 = never)")
    args = ap.parse_args()

    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    now = int(time.time() * 1000)
    expires = str(now + args.days * 86400_000) if args.days else "NULL"
    ident = secrets.token_hex(8)

    def q(v: str) -> str:
        return "NULL" if not v else "'" + v.replace("'", "''") + "'"

    print("=" * 70)
    print("TOKEN (shown once — copy it now, it is not recoverable):\n")
    print(f"    {token}\n")
    print("=" * 70)
    print("\nRun this against D1 (Cloudflare dashboard → D1 → Console, or CI):\n")
    print(
        f"INSERT INTO actors (id, token_hash, role, name, org, scope_province,\n"
        f"                    created_at, expires_at, active)\n"
        f"VALUES ('{ident}', '{digest}', '{args.role}', {q(args.name)}, {q(args.org)},\n"
        f"        {q(args.scope)}, {now}, {expires}, 1);"
    )
    print("\nTo revoke later:")
    print(f"    UPDATE actors SET active = 0 WHERE id = '{ident}';")
    if not args.scope and args.role != "admin":
        print("\n  NOTE: no --scope given, so this account can read requests nationwide.")
        print("        Scope district responders to their province.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
