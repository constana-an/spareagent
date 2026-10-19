"""Operator-local: prepare one-click sharing. Never enables anything by itself.

Checks that the chosen authorization is operator-verified, route-covered, unrevoked and
unexpired (it becomes the template every self-serve group copies), generates a signup code
into a new 0600 file, and prints the environment lines to add to the server's .env.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path

from apps.api.config import load_settings
from db.models import ExperimentalAuthorization
from services.context import build_context
from services.experiments.self_serve import authorization_usable


async def check(authorization_id: uuid.UUID) -> ExperimentalAuthorization:
    ctx = build_context(load_settings())
    try:
        async with ctx.sessionmaker() as session:
            auth = await session.get(ExperimentalAuthorization, authorization_id)
    finally:
        await ctx.aclose()
    if not authorization_usable(auth, datetime.now(UTC)):
        raise SystemExit(
            "authorization is missing, unverified, not route-covered, revoked or expired"
        )
    return auth


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--authorization-id", type=uuid.UUID, required=True)
    p.add_argument("--code-file", type=Path, required=True, help="new file for the signup code")
    args = p.parse_args()
    if args.code_file.exists():
        p.error("code file already exists")
    auth = asyncio.run(check(args.authorization_id))
    code = secrets.token_urlsafe(24)
    fd = os.open(args.code_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(code + "\n")
    print(f"Template: {auth.authorization_reference} (max {auth.max_participants} participants,")
    print(f"expires {auth.expires_at:%Y-%m-%d}). Signup code written to {args.code_file}.")
    print("Add to the server .env, then restart api/scheduler/verifier:")
    print("  SELF_SERVE_ENABLED=true")
    print(f"  SELF_SERVE_AUTHORIZATION_ID={auth.id}")
    print(f"  SELF_SERVE_SIGNUP_CODE=<contents of {args.code_file}>")


if __name__ == "__main__":
    main()
