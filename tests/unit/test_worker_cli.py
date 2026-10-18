import os
import stat

import pytest

from packages.worker.agent import Backoff
from packages.worker.cli import build_parser, main
from packages.worker.lease_client import validate_api_url
from packages.worker.state import NodeState, load_state, save_state, state_dir


@pytest.mark.parametrize(
    ("url", "insecure"),
    [
        ("https://api.example.com", False),
        ("https://api-tls:8443/", False),
        ("http://localhost:8000", True),
        ("http://127.0.0.1:8000", True),
    ],
)
def test_allowed_api_urls(url, insecure):
    assert validate_api_url(url, insecure) == url.rstrip("/")


@pytest.mark.parametrize(
    ("url", "insecure"),
    [
        ("http://api.example.com", False),
        ("http://api.example.com", True),  # insecure only for loopback
        ("http://10.0.0.5:8000", True),
        ("ftp://api.example.com", True),
    ],
)
def test_plain_http_refused(url, insecure):
    with pytest.raises(ValueError):
        validate_api_url(url, insecure)


def test_state_file_is_private_and_hides_key(tmp_path):
    d = state_dir(str(tmp_path / "home"))
    st = NodeState("https://x", "w", "sa_nk_secret", "2030-01-01T00:00:00+00:00", "r", "n")
    save_state(d, st)
    mode = stat.S_IMODE(os.stat(d / "node.json").st_mode)
    assert mode == 0o600
    assert load_state(d) == st
    assert "node_key" not in st.public_view()


def test_backoff_grows_and_caps():
    b = Backoff(1.0, 8.0)
    delays = [b.next_delay() for _ in range(6)]
    assert all(0 < d <= 8.0 for d in delays)
    assert delays[-1] > 2.0
    b.reset()
    assert b.next_delay() <= 1.0


def test_parser_has_required_commands():
    parser = build_parser()
    for cmd in ("register", "start", "status", "stop"):
        assert parser.parse_args([cmd]).command == cmd


def test_register_requires_arguments(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("SPAREAGENT_API_URL", raising=False)
    assert main(["--state-dir", str(tmp_path), "register"]) == 2


def test_status_unregistered(tmp_path, capsys):
    assert main(["--state-dir", str(tmp_path), "status"]) == 1
    assert '"registered": false' in capsys.readouterr().out


def test_stop_when_not_running(tmp_path, capsys):
    assert main(["--state-dir", str(tmp_path), "stop"]) == 0
