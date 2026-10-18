import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from apps.api.config import Settings
from services.artifacts.store import ArtifactIntegrityError, ArtifactKeyError, LocalArtifactStore
from services.observability import REDACTED, JsonFormatter, RedactingFilter, redact

ROOT = Path(__file__).resolve().parents[2]
TASK = "0b9c6c3e-6f0a-4d39-9d0e-2f7b9b1d7a11"


# ---------------------------------------------------------------- SEC-001 redaction
@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-abcdefghijklmnopqrstuvwxyz123456",
        "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "AKIAABCDEFGHIJKLMNOP",
        "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
    ],
)
def test_sec_001_secret_patterns_redacted(secret):
    out = redact(f"calling upstream with {secret} now")
    assert secret not in out
    assert REDACTED in out


def test_key_value_secrets_redacted_but_key_kept():
    out = redact("api_key=supersecretvalue password: hunter2hunter2")
    assert "supersecretvalue" not in out and "hunter2hunter2" not in out
    assert "api_key=" in out and "password:" in out


def test_sec_001_log_records_redacted():
    secret = "sk-test-0123456789abcdefghijklmnop"
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "token %s", (secret,), None)
    record.upstream = f"Authorization: Bearer {secret}"
    assert RedactingFilter().filter(record)
    rendered = JsonFormatter().format(record)
    assert secret not in rendered
    assert json.loads(rendered)["msg"] == f"token {REDACTED}"


# ---------------------------------------------------------------- SEC-002 artifact keys
@pytest.mark.parametrize(
    "key",
    [
        "../etc/passwd",
        f"{TASK}/../../etc/passwd",
        "/etc/passwd",
        f"{TASK}/aa/{'a' * 63}",
        f"{TASK}/aa/{'a' * 64}/../../x",
        f"{TASK}\\aa\\{'a' * 64}",
        "",
    ],
)
async def test_sec_002_traversal_keys_rejected(tmp_path, key):
    store = LocalArtifactStore(tmp_path)
    with pytest.raises(ArtifactKeyError):
        await store.get(key)


async def test_artifact_round_trip_and_integrity(tmp_path):
    store = LocalArtifactStore(tmp_path / "root")
    key = await store.put(TASK, "summary.txt", b"hello", "text/plain")
    assert key.startswith(f"{TASK}/")
    assert await store.get(key) == b"hello"
    # content addressing: same bytes -> same key
    assert await store.put(TASK, "again.txt", b"hello", "text/plain") == key
    (tmp_path / "root" / key).write_bytes(b"tampered")
    with pytest.raises(ArtifactIntegrityError):
        await store.get(key)


async def test_symlink_escape_rejected(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    outside.mkdir()
    store = LocalArtifactStore(root)
    digest = "b" * 64
    (root / TASK).mkdir(parents=True)
    os.symlink(outside, root / TASK / "bb")
    (outside / digest).write_bytes(b"x")
    with pytest.raises(ArtifactKeyError):
        await store.get(f"{TASK}/bb/{digest}")


# ---------------------------------------------------------------- SEC-003 config
@pytest.mark.parametrize("env", ["production", "staging", "test"])
def test_sec_003_dev_auth_rejected_outside_local(env):
    with pytest.raises(ValidationError, match="DEV_AUTH_ONLY"):
        Settings(app_env=env, dev_auth_only=True)


def test_dev_auth_allowed_locally():
    assert Settings(app_env="local", dev_auth_only=True).dev_auth_only


def test_unknown_provider_mode_fails():
    with pytest.raises(ValidationError, match="PROVIDER_MODE"):
        Settings(provider_mode="chatgpt-subscription")


def test_invalid_trusted_cidr_fails():
    with pytest.raises(ValidationError):
        Settings(dev_auth_trusted_cidrs="not-a-cidr")


def test_sec_003_app_process_refuses_to_start():
    env = {
        **os.environ,
        "APP_ENV": "production",
        "DEV_AUTH_ONLY": "true",
        "PYTHONPATH": str(ROOT),
    }
    proc = subprocess.run(
        [sys.executable, "-c", "from apps.api.main import create_app; create_app()"],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode != 0
    assert "DEV_AUTH_ONLY" in proc.stderr
