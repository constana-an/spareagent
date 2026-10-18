"""Persistent local node identity (`~/.spareagent-worker/node.json`, mode 0600).

Holds only the node's own credential (node key) and connection settings. It never contains
database, Redis or platform control-plane credentials.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

STATE_FILE = "node.json"
PID_FILE = "worker.pid"


@dataclass
class NodeState:
    api_url: str
    worker_id: str
    node_key: str
    node_key_expires_at: str
    resource_id: str
    name: str
    max_concurrency: int = 1
    ca_file: str | None = None
    allow_insecure_http: bool = False

    def public_view(self) -> dict:
        data = asdict(self)
        data.pop("node_key")
        return data


def state_dir(explicit: str | None = None) -> Path:
    raw = explicit or os.environ.get("SPAREAGENT_WORKER_HOME") or "~/.spareagent-worker"
    path = Path(raw).expanduser()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def load_state(directory: Path) -> NodeState | None:
    path = directory / STATE_FILE
    if not path.exists():
        return None
    return NodeState(**json.loads(path.read_text(encoding="utf-8")))


def save_state(directory: Path, state: NodeState) -> None:
    path = directory / STATE_FILE
    tmp = directory / f".{STATE_FILE}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(asdict(state), fh, indent=2)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def read_pid(directory: Path) -> int | None:
    try:
        return int((directory / PID_FILE).read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def write_pid(directory: Path) -> None:
    (directory / PID_FILE).write_text(str(os.getpid()))


def clear_pid(directory: Path) -> None:
    try:
        if read_pid(directory) == os.getpid():
            (directory / PID_FILE).unlink()
    except FileNotFoundError:
        pass
