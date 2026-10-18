"""Owner-side controls that live only on the owner's device.

`sharing_paused` is a local kill switch: while it is set, this node refuses every
experimental (cross-user) lease before any work starts, regardless of what the server
says. It complements the server-side pause/revoke in ExperimentalParticipationService and
is the control an owner can use even when the SpareAgent API is unreachable.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

PAUSE_FILE = "sharing_paused"


class LocalSharingControl:
    def __init__(self, state_dir: Path) -> None:
        self.path = Path(state_dir) / PAUSE_FILE

    def pause(self, reason: str = "owner paused sharing") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {"paused_at": datetime.now(UTC).isoformat(), "reason": reason[:200]}
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh)

    def resume(self) -> None:
        self.path.unlink(missing_ok=True)

    @property
    def paused(self) -> bool:
        return self.path.exists()

    def allows_shared_work(self) -> bool:
        return not self.paused
