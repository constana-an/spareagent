"""Operator-local provisioning. Creates invitations, never activates LIVE or sharing.

Requires server database access. The secret output file is mode 0600. Deliver each code
privately yourself; this script never emails or sends messages to participants.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from apps.api.config import load_settings
from db.models import FriendsLoginInvite, User
from services.context import build_context
from services.experiments.participation import ExperimentalParticipationService, _audit
from services.friends_auth import digest
from services.tasks.service import Principal


async def provision(args):
    ctx = build_context(load_settings())
    try:
        async with ctx.sessionmaker() as session, session.begin():
            operator = (
                await session.execute(select(User).where(User.email == args.operator))
            ).scalar_one_or_none()
            if operator is None:
                count = (await session.execute(select(func.count()).select_from(User))).scalar_one()
                if count != 0:
                    raise ValueError("Operator must already be ADMIN in a nonempty database")
                operator = User(email=args.operator, role="ADMIN")
                session.add(operator)
                await session.flush()
            if operator.role != "ADMIN":
                raise ValueError("Operator must be ADMIN")
            people = []
            for email in (args.alice, args.bob):
                user = (
                    await session.execute(select(User).where(User.email == email))
                ).scalar_one_or_none()
                if user is None:
                    user = User(email=email, role="CONSUMER")
                    session.add(user)
                    await session.flush()
                people.append(user)
        principal = Principal(operator.id, "ADMIN")
        service = ExperimentalParticipationService(ctx)
        group = await service.create_group(
            principal,
            args.group,
            {
                "max_members": 2,
                "max_concurrent_tasks": 1,
                "max_tasks_per_day": 3,
                "max_task_duration_seconds": 60,
                "allowed_task_types": ["CODE_REVIEW"],
            },
        )
        for user in people:
            await service.invite(principal, group.id, user.id)
        result = {
            "group_id": str(group.id),
            "LIVE": "OFF; manual operator activation required",
            "logins": {},
        }
        async with ctx.sessionmaker() as session, session.begin():
            for user in [operator, *people]:
                token = secrets.token_urlsafe(32)
                expires = datetime.now(UTC) + timedelta(minutes=15)
                session.add(
                    FriendsLoginInvite(
                        user_id=user.id, token_digest=digest(token), expires_at=expires
                    )
                )
                _audit(session, operator.id, "friends.invite", "user", user.id)
                result["logins"][user.email] = {
                    "user_id": str(user.id),
                    "token": token,
                    "expires_at": expires.isoformat(),
                }
        # Exclusive creation avoids silently overwriting previously issued credentials.
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(result, output, indent=2)
        print(f"Created a two-person group; private login codes written to {args.output}")
    finally:
        await ctx.aclose()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--operator", required=True, help="existing ADMIN email; bootstraps only an empty DB"
    )
    p.add_argument("--alice", required=True)
    p.add_argument("--bob", required=True)
    p.add_argument("--group", default="Friends beta")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if len({args.operator, args.alice, args.bob}) != 3:
        p.error("operator, Alice and Bob must be distinct")
    if args.output.exists():
        p.error("output file already exists")
    asyncio.run(provision(args))


if __name__ == "__main__":
    main()
