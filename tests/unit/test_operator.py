"""Maintenance must preserve existing identities, switches, routes, and deliverables."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "spareagent_operator", Path(__file__).resolve().parents[2] / "scripts/operator_tool.py"
)
operator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(operator)


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    directory = tmp_path / "private-deployment"
    directory.mkdir()
    (directory / ".env").write_text("EXPERIMENT_LIVE_ENABLED=false\nPASSWORD=saved\n")
    (directory / "compose.yaml").write_text("name: pilot\n")
    monkeypatch.setattr(operator.shutil, "which", lambda name: f"/test/{name}")
    instance = operator.Operator(directory, check=True)
    configuration = {
        "services": {
            "api": {
                "environment": {
                    "SELF_SERVE_ENABLED": "true",
                    "SELF_SERVE_SIGNUP_CODE": "fixture-not-a-real-code",
                    "FRIENDS_WEB_ORIGIN": "https://fixture.ts.net:8443",
                    "EXPERIMENT_LIVE_ENABLED": "false",
                }
            },
            "postgres": {"environment": {"POSTGRES_USER": "test", "POSTGRES_DB": "test"}},
            "tls": {
                "ports": [{"published": str(p), "host_ip": "127.0.0.1"} for p in (13000, 18000)]
            },
        },
        "volumes": {"pgdata": {"name": "existing-volume"}},
    }
    routes = {
        "TCP": {"443": {"HTTPS": True}},
        "Web": {"fixture.ts.net:443": {"Handlers": {"/": {"Proxy": "http://other-app"}}}},
        "AllowFunnel": {"fixture.ts.net:443": True},
    }
    for public, local in ((8443, 13000), (8444, 18000)):
        routes["TCP"][str(public)] = {"HTTPS": True}
        routes["Web"][f"fixture.ts.net:{public}"] = {
            "Handlers": {"/": {"Proxy": f"http://127.0.0.1:{local}"}}
        }
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[1:] == ["status", "--json"]:
            return json.dumps({"BackendState": "Running", "Self": {"DNSName": "fixture.ts.net."}})
        if args[1:] == ["serve", "status", "--json"]:
            return json.dumps(routes)
        return ""

    snapshot = {
        "authorization_ok": True,
        "busy": 0,
        "counts": {"tasks": 4},
    }
    monkeypatch.setattr(instance, "run", run)
    monkeypatch.setattr(instance, "config", lambda: configuration)
    monkeypatch.setattr(instance, "snapshot", lambda: snapshot)
    monkeypatch.setattr(instance, "health", lambda: None)
    return SimpleNamespace(
        instance=instance,
        config=configuration,
        routes=routes,
        calls=calls,
        snapshot=snapshot,
        directory=directory,
    )


def test_inherited_environment_cannot_enable_live_or_replace_compose(deployment, monkeypatch):
    monkeypatch.setenv("EXPERIMENT_LIVE_ENABLED", "true")
    monkeypatch.setenv("COMPOSE_FILE", "unrelated.yaml")
    monkeypatch.setenv("PASSWORD", "inherited")
    monkeypatch.setenv("PATH", "/keep/tools")
    env = operator.compose_environment(deployment.directory)
    assert "EXPERIMENT_LIVE_ENABLED" not in env
    assert "COMPOSE_FILE" not in env and "PASSWORD" not in env
    assert env["PATH"] == "/keep/tools"


def test_check_is_read_only_and_preserves_disabled_live_and_other_funnel(deployment):
    result = deployment.instance.apply()
    assert result["live_preserved"] == "false"
    assert result["healthy"]
    assert all("up" not in args and "--bg" not in args for args in deployment.calls)
    assert not (deployment.directory / "private").exists()


@pytest.mark.parametrize("conflict", ["proxy", "funnel", "tcp"])
def test_conflicting_route_is_not_overwritten(deployment, conflict):
    if conflict == "proxy":
        deployment.routes["Web"]["fixture.ts.net:8443"]["Handlers"]["/"]["Proxy"] = "http://other"
    elif conflict == "funnel":
        deployment.routes["AllowFunnel"]["fixture.ts.net:8443"] = True
    else:
        deployment.routes["TCP"]["8443"] = {"TCPForward": "localhost:22"}
    with pytest.raises(operator.OperatorError, match="不自动"):
        deployment.instance.apply()
    assert all("--bg" not in args and "up" not in args for args in deployment.calls)


def test_public_dashboard_turns_on_funnel_for_the_web_port_only(deployment, monkeypatch, tmp_path):
    instance = deployment.instance
    instance.check = False
    instance.work = tmp_path
    (deployment.directory / "operator.json").write_text(
        json.dumps(
            {
                "dashboard_url": "https://fixture.ts.net:8443",
                "api_url": "https://fixture.ts.net:8444",
                "public_dashboard": True,
            }
        )
    )
    monkeypatch.setattr(instance, "build_python", lambda: "/test/python")
    monkeypatch.setattr(instance, "backup", lambda snapshot: None)
    monkeypatch.setattr(instance, "package", lambda python: {"reused": True})
    assert instance.apply()["healthy"]
    changes = [args[1:] for args in deployment.calls if "--bg" in args]
    assert changes == [["funnel", "--bg", "--https=8443", "http://127.0.0.1:13000"]]


def test_public_dashboard_still_refuses_funnel_on_the_api_port(deployment):
    (deployment.directory / "operator.json").write_text(
        json.dumps(
            {
                "dashboard_url": "https://fixture.ts.net:8443",
                "api_url": "https://fixture.ts.net:8444",
                "public_dashboard": True,
            }
        )
    )
    deployment.routes["AllowFunnel"]["fixture.ts.net:8443"] = True
    deployment.routes["AllowFunnel"]["fixture.ts.net:8444"] = True
    with pytest.raises(operator.OperatorError, match="8444.*Funnel"):
        deployment.instance.apply()


@pytest.mark.parametrize("busy,authorized", [(1, True), (0, False)])
def test_tasks_or_invalid_authorization_stop_maintenance(deployment, busy, authorized):
    deployment.snapshot.update(busy=busy, authorization_ok=authorized)
    with pytest.raises(operator.OperatorError):
        deployment.instance.apply()
    assert not (deployment.directory / "operator.json").exists()


def test_missing_route_check_does_not_repair(deployment):
    deployment.routes["Web"].pop("fixture.ts.net:8444")
    deployment.routes["TCP"].pop("8444")
    with pytest.raises(operator.OperatorError, match="入口缺失"):
        deployment.instance.apply()
    assert all("--bg" not in args for args in deployment.calls)


def test_apply_backs_up_before_up_and_allows_normal_data_changes(deployment, monkeypatch, tmp_path):
    instance = deployment.instance
    instance.check = False
    instance.work = tmp_path
    events = []
    monkeypatch.setattr(instance, "build_python", lambda: "/test/python")
    monkeypatch.setattr(instance, "backup", lambda snapshot: events.append("backup"))
    previous_run = instance.run
    before = json.loads(json.dumps(deployment.snapshot))
    snapshots = []

    def snapshot():
        snapshots.append(True)
        return before

    def run(args, **kwargs):
        if "up" in args and args[-1] == "120":
            assert events == ["backup"]
            events.append("up")
            deployment.snapshot["counts"]["tasks"] += 1
        return previous_run(args, **kwargs)

    monkeypatch.setattr(instance, "snapshot", snapshot)
    monkeypatch.setattr(instance, "run", run)
    monkeypatch.setattr(instance, "package", lambda python: {"reused": True})
    monkeypatch.setattr(instance, "rebuild", lambda: pytest.fail("rebuild is opt-in"))
    result = instance.apply()
    assert events == ["backup", "up"]
    assert len(snapshots) == 1
    assert result["healthy"] and result["counts_before"] == {"tasks": 4}
    assert deployment.snapshot["counts"] == {"tasks": 5}
    assert (deployment.directory / ".env").read_text().startswith("EXPERIMENT_LIVE_ENABLED=false")


def test_failed_image_build_keeps_saved_image_and_live_settings(deployment, monkeypatch):
    original = (deployment.directory / ".env").read_bytes()
    monkeypatch.setattr(operator, "fingerprint", lambda *args: "fixture-image-digest")

    def failed_build(*args, **kwargs):
        raise operator.OperatorError("build failed")

    monkeypatch.setattr(deployment.instance, "run", failed_build)
    with pytest.raises(operator.OperatorError):
        deployment.instance.rebuild()
    assert (deployment.directory / ".env").read_bytes() == original


def test_failed_package_publish_restores_previous_bundle_and_installer(tmp_path):
    stage, previous, backup = (tmp_path / n for n in ("stage", "current", "backup"))
    for directory in (stage, previous, backup):
        directory.mkdir()
    (stage / "wheel").write_text("new")
    (previous / "wheel").write_text("old")
    published = tmp_path / "installer.zip"
    published.write_bytes(b"old zip")
    with pytest.raises(FileNotFoundError):
        operator.publish_package(stage, previous, tmp_path / "missing.zip", published, backup)
    assert (previous / "wheel").read_text() == "old"
    assert published.read_bytes() == b"old zip"


def test_manifest_rejects_tampering_and_paths_outside_bundle(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    wheel = bundle / "worker.whl"
    wheel.write_bytes(b"valid")
    (bundle / "SHA256SUMS").write_text(f"{operator.digest(wheel)}  worker.whl\n")
    assert operator.verify_manifest(bundle)
    wheel.write_bytes(b"tampered")
    assert not operator.verify_manifest(bundle)
    (bundle / "SHA256SUMS").write_text(f"{operator.digest(wheel)}  ../outside.whl\n")
    assert not operator.verify_manifest(bundle)


def test_only_existing_private_origins_and_uuid_are_accepted():
    assert operator.origin("https://fixture.ts.net:8443") == ("fixture.ts.net", 8443)
    for url in ("http://fixture.ts.net", "https://example.com", "https://fixture.ts.net/path"):
        with pytest.raises(operator.OperatorError):
            operator.origin(url)
    with pytest.raises(ValueError):
        operator.snapshot_sql("'; DELETE FROM users; --")


def test_private_command_stdout_is_not_written_to_log(deployment, monkeypatch, tmp_path):
    instance = deployment.instance
    instance.log = tmp_path / "operation.log"
    monkeypatch.setattr(
        operator.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout=b"fixture-secret", stderr=b"", returncode=0),
    )
    assert operator.Operator.run(instance, ["fixture"], private=True) == "fixture-secret"
    assert "fixture-secret" not in instance.log.read_text()
