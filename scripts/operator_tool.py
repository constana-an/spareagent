"""Maintain the existing private Mac deployment without reseeding data or enabling LIVE."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", "__pycache__", "node_modules", ".next", "build", ".DS_Store"}


class OperatorError(Exception):
    pass


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def private_write(path: Path, value: str) -> None:
    fd, name = tempfile.mkstemp(prefix=".operator-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as target:
            target.write(value)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def fingerprint(paths: list[Path], settings: dict) -> str:
    result = hashlib.sha256(json.dumps(settings, sort_keys=True).encode())
    for top in paths:
        for path in sorted(top.rglob("*") if top.is_dir() else [top]):
            if path.is_file() and not any(p in SKIP or p.endswith(".egg-info") for p in path.parts):
                result.update(str(path.relative_to(ROOT)).encode())
                result.update(path.read_bytes())
    return result.hexdigest()


def origin(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not (parts.hostname or "").endswith(".ts.net")
        or parts.username
        or parts.password
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise OperatorError("当前入口只支持已有的 Tailscale HTTPS 地址。请检查 operator.json。")
    return parts.hostname, parts.port or 443


def compose_environment(directory: Path) -> dict[str, str]:
    # Shell variables must not silently override the operator's saved switches or secrets.
    texts = [(directory / ".env").read_text()]
    texts += [p.read_text() for p in directory.glob("compose*.yaml")]
    keys = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", "\n".join(texts)))
    keys.update(re.findall(r"^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)=", texts[0], re.M))
    return {k: v for k, v in os.environ.items() if k not in keys and not k.startswith("COMPOSE_")}


def verify_manifest(directory: Path) -> bool:
    try:
        lines = (directory / "SHA256SUMS").read_text().splitlines()
        if not lines:
            return False
        for line in lines:
            expected, name = line.split(maxsplit=1)
            path = (directory / name).resolve()
            if not path.is_relative_to(directory.resolve()) or digest(path) != expected:
                return False
        return True
    except (OSError, ValueError):
        return False


def snapshot_sql(authorization: str) -> str:
    authorization = str(uuid.UUID(authorization))
    return f"""SELECT json_build_object(
      'authorization_ok',EXISTS(SELECT 1 FROM experimental_authorizations
        WHERE id='{authorization}' AND verification_status='OPERATOR_VERIFIED'
        AND integration_route_covered AND integration_route='CODEX_CLI_EXEC'
        AND revoked_at IS NULL AND expires_at > now()),
      'busy',(SELECT count(*) FROM tasks
        WHERE status NOT IN ('SUCCEEDED','FAILED','CANCELLED','TIMED_OUT')),
      'counts',json_build_object('nodes',(SELECT count(*) FROM worker_nodes),
        'members',(SELECT count(*) FROM experimental_group_members),
        'tasks',(SELECT count(*) FROM tasks),'artifacts',(SELECT count(*) FROM artifacts)));"""


class Operator:
    def __init__(self, directory: Path, *, check: bool = False, arch: str = platform.machine()):
        self.directory = directory.resolve()
        if (
            not (self.directory / ".env").is_file()
            or not (self.directory / "compose.yaml").is_file()
        ):
            raise OperatorError(
                "未找到已有私有部署的 .env/compose.yaml。"
                "请用 --private-dir 指定部署目录；本入口不新建授权或数据库。"
            )
        self.check = check
        self.arch = arch
        self.step = "检查配置"
        self.work: Path | None = None
        self.log: Path | None = None
        self.env = compose_environment(self.directory)
        docker = shutil.which("docker")
        if not docker:
            raise OperatorError("未找到 Docker，请先安装并打开 Docker Desktop。")
        self.compose = [
            docker,
            "compose",
            "--project-directory",
            str(self.directory),
            "--env-file",
            str(self.directory / ".env"),
        ]
        self.docker = docker
        self.tailscale = (
            shutil.which("tailscale") or "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
        )

    def progress(self, text: str) -> None:
        self.step = text
        print(f"→ {text}", flush=True)

    def run(self, args: list[str], *, private: bool = False, stdout=None) -> str:
        result = subprocess.run(
            args,
            cwd=ROOT,
            env=self.env,
            stdout=stdout or subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        output = (result.stdout or b"").decode(errors="replace")
        error = result.stderr.decode(errors="replace")
        if self.log:
            with self.log.open("a") as target:
                target.write(f"\n[{self.step}]\n{'' if private else output}{error}")
        if result.returncode:
            raise OperatorError(f"{self.step}失败（退出码 {result.returncode}）。")
        return output

    def config(self) -> dict:
        return json.loads(self.run([*self.compose, "config", "--format", "json"], private=True))

    def snapshot(self) -> dict:
        return json.loads(
            self.run(
                [
                    *self.compose,
                    "exec",
                    "-T",
                    "postgres",
                    "psql",
                    "-U",
                    self.db_user,
                    "-d",
                    self.db_name,
                    "-At",
                    "-c",
                    snapshot_sql(self.api_env["SELF_SERVE_AUTHORIZATION_ID"]),
                ],
                private=True,
            )
        )

    def preflight(self) -> dict:
        self.progress("检查 Docker、已保存的配置和授权")
        self.run([self.docker, "info", "--format", "{{.ServerVersion}}"])
        config = self.config()
        self.api_env = config["services"]["api"]["environment"]
        postgres = config["services"]["postgres"]["environment"]
        self.db_user, self.db_name = postgres["POSTGRES_USER"], postgres["POSTGRES_DB"]
        if str(self.api_env.get("SELF_SERVE_ENABLED")).lower() != "true":
            raise OperatorError("自助注册当前未开启；本入口不会代为启用，请先检查运营配置。")
        if not self.api_env.get("SELF_SERVE_SIGNUP_CODE"):
            raise OperatorError("当前配置缺少注册码；不会自动更换注册码或新建授权。")
        connection_file = self.directory / "operator.json"
        self.connection = (
            json.loads(connection_file.read_text())
            if connection_file.exists()
            else {
                "dashboard_url": self.api_env["FRIENDS_WEB_ORIGIN"],
                "api_url": f"https://{urlsplit(self.api_env['FRIENDS_WEB_ORIGIN']).hostname}:8444",
            }
        )
        web_host, web_port = origin(self.connection["dashboard_url"])
        api_host, api_port = origin(self.connection["api_url"])
        if (
            web_host != api_host
            or web_port == api_port
            or self.connection["dashboard_url"] != self.api_env["FRIENDS_WEB_ORIGIN"]
        ):
            raise OperatorError("网页/API 地址与已保存配置不一致，请检查 operator.json。")
        self.progress("检查 Tailscale 私网入口，保留其他应用的配置")
        status = json.loads(self.run([self.tailscale, "status", "--json"], private=True))
        if (
            status.get("BackendState") != "Running"
            or status.get("Self", {}).get("DNSName", "").rstrip(".") != web_host
        ):
            raise OperatorError("Tailscale 未连接，或当前设备与配置的主机名不同。")
        self.serve_before = json.loads(
            self.run([self.tailscale, "serve", "status", "--json"], private=True)
        )
        self.missing_routes = []
        ports = config["services"]["tls"]["ports"]
        for public_port, local_port in ((web_port, 13000), (api_port, 18000)):
            if not any(
                str(p.get("published")) == str(local_port) and p.get("host_ip") == "127.0.0.1"
                for p in ports
            ):
                raise OperatorError(f"缺少回环上游端口 {local_port}，请先应用私网 HTTPS 配置。")
            key = f"{web_host}:{public_port}"
            proxy = f"http://127.0.0.1:{local_port}"
            current = self.serve_before.get("Web", {}).get(key)
            tcp = self.serve_before.get("TCP", {}).get(str(public_port))
            if tcp and (tcp != {"HTTPS": True} or not current):
                raise OperatorError(f"端口 {public_port} 已用于其他转发，不自动覆盖。")
            if self.serve_before.get("AllowFunnel", {}).get(key):
                raise OperatorError(f"端口 {public_port} 已用于公网 Funnel，不自动改动。")
            if current and current != {"Handlers": {"/": {"Proxy": proxy}}}:
                raise OperatorError(f"端口 {public_port} 被其他入口占用，不自动覆盖。")
            if not current:
                self.missing_routes.append((public_port, proxy))
        self.progress("检查现有数据库与运行任务")
        volume = config["volumes"]["pgdata"]["name"]
        self.run([self.docker, "volume", "inspect", volume], private=True)
        if not self.check:
            self.run(
                [*self.compose, "up", "-d", "--wait", "--wait-timeout", "90", "postgres", "redis"]
            )
        before = self.snapshot()
        if not before["authorization_ok"]:
            raise OperatorError("现有授权不可用或已到期；不会自动创建或延长授权。")
        if before["busy"]:
            raise OperatorError("仍有未结束的任务，请等待完成或处理后再维护。")
        self.live = str(self.api_env.get("EXPERIMENT_LIVE_ENABLED", "false")).lower()
        return before

    def start_run(self) -> None:
        parent = self.directory / "private/operator-runs"
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.work = Path(
            tempfile.mkdtemp(prefix=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-"), dir=parent)
        )
        self.log = self.work / "operation.log"
        private_write(self.log, "")

    def backup(self, before: dict) -> None:
        self.progress("备份现有配置、数据库和交付文件")
        for name in (".env", "compose.yaml", "compose.lan.yaml", "nginx.conf", "operator.json"):
            source = self.directory / name
            if source.exists():
                shutil.copyfile(source, self.work / name)
                (self.work / name).chmod(0o600)
        private_write(self.work / "before.json", json.dumps(before, indent=2))
        private_write(self.work / "tailscale-serve.json", json.dumps(self.serve_before, indent=2))
        for name, command in (
            (
                "database.dump",
                [
                    *self.compose,
                    "exec",
                    "-T",
                    "postgres",
                    "pg_dump",
                    "-U",
                    self.db_user,
                    "-d",
                    self.db_name,
                    "-Fc",
                ],
            ),
            (
                "artifacts.tar",
                [
                    self.docker,
                    "cp",
                    self.run([*self.compose, "ps", "-a", "-q", "api"]).strip()
                    + ":/var/lib/spareagent/artifacts/.",
                    "-",
                ],
            ),
        ):
            target = self.work / name
            with target.open("wb") as output:
                target.chmod(0o600)
                self.run(command, stdout=output)
            if target.stat().st_size == 0:
                raise OperatorError(f"备份文件 {name} 为空，已停止。")

    def build_python(self) -> str:
        candidates = [
            sys.executable,
            shutil.which("python3.12"),
            "/opt/miniconda3/bin/python",
            str(Path.home() / "miniconda3/bin/python"),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                result = subprocess.run(
                    [
                        candidate,
                        "-c",
                        "import sys,setuptools,wheel; assert sys.version_info >= (3,12); "
                        "assert int(setuptools.__version__.split('.')[0]) >= 69",
                    ],
                    capture_output=True,
                    check=False,
                )
                if result.returncode == 0:
                    return candidate
        raise OperatorError("未找到 Python 3.12+ 的 setuptools/wheel 构建工具，请安装后重试。")

    def rebuild(self) -> None:
        self.progress("构建当前源码的服务镜像")
        revision = fingerprint(
            [
                ROOT / p
                for p in (
                    "Dockerfile",
                    "requirements.txt",
                    "requirements-dev.txt",
                    "pyproject.toml",
                    "apps",
                    "packages",
                    "services",
                    "db",
                    "scripts/migrate.py",
                )
            ],
            {},
        )[:16]
        images = {
            "APP_IMAGE": f"spareagent-pilot-app:local-{revision}",
            "WEB_IMAGE": f"spareagent-pilot-web:local-{revision}",
            "SOURCE_REVISION": revision,
        }
        for key, context in (("APP_IMAGE", ROOT), ("WEB_IMAGE", ROOT / "apps/web")):
            self.run([self.docker, "build", "-t", images[key], str(context)])
        env_path = self.directory / ".env"
        text = env_path.read_text()
        for key, value in images.items():
            text, count = re.subn(rf"^{key}=.*$", f"{key}={value}", text, flags=re.M)
            if not count:
                text += f"\n{key}={value}\n"
        private_write(env_path, text)

    def health(self) -> None:
        self.progress("验证网页和 API 的 HTTPS 健康状态")
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )
        for url in (
            self.connection["api_url"].rstrip("/") + "/readyz",
            self.connection["dashboard_url"],
        ):
            with opener.open(url, timeout=15) as response:
                if response.status != 200:
                    raise OperatorError("HTTPS 健康检查未通过。")
                if url.endswith("/readyz") and json.load(response).get("status") != "ready":
                    raise OperatorError("API 尚未就绪。")

    def package(self, python: str) -> dict:
        self.progress("检查并生成免费本机安装包")
        terms = self.run(
            [
                *self.compose,
                "exec",
                "-T",
                "api",
                "python",
                "-c",
                "from apps.api.config import load_settings; "
                "print(load_settings().experiment_terms_version)",
            ]
        ).strip()
        cache = ROOT / "output/worker-bundle"
        published = ROOT / f"output/macos-app/SpareAgent-local-install-{self.arch}.zip"
        state_file = self.directory / "private/operator-state.json"
        signature = fingerprint(
            [
                ROOT / p
                for p in (
                    "packages/worker",
                    "packages/provider_sdk",
                    "packages/common",
                    "packages/__init__.py",
                    "apps/macos",
                    "scripts/build_worker_bundle.py",
                    "scripts/build_macos_app.py",
                    "scripts/Install-SpareAgent.sh",
                    "scripts/SpareAgent.command",
                    "scripts/runtime-pins.json",
                    "docs/LOCAL_APP_INSTALL.md",
                )
            ],
            {
                **self.connection,
                "terms": terms,
                "arch": self.arch,
                "signup_digest": hashlib.sha256(
                    self.api_env["SELF_SERVE_SIGNUP_CODE"].encode()
                ).hexdigest(),
            },
        )
        previous = json.loads(state_file.read_text()) if state_file.exists() else {}
        if (
            previous.get("signature") == signature
            and published.exists()
            and previous.get("sha256") == digest(published)
            and verify_manifest(cache)
        ):
            print("  安装包未变化，复用已校验的文件。", flush=True)
            return {"path": str(published), "sha256": digest(published), "reused": True}
        stage = self.work / "worker-bundle"
        code_file = self.work / "signup-code.txt"
        private_write(code_file, self.api_env["SELF_SERVE_SIGNUP_CODE"])
        command = [
            python,
            str(ROOT / "scripts/build_worker_bundle.py"),
            "--api-url",
            self.connection["api_url"],
            "--dashboard-url",
            self.connection["dashboard_url"],
            "--signup-code-file",
            str(code_file),
            "--terms-version",
            terms,
            "--output-dir",
            str(stage),
            "--no-build-isolation",
        ]
        if verify_manifest(cache):
            try:
                self.run([*command, "--dependency-cache", str(cache / "wheels")])
            except OperatorError:
                self.progress("从包仓库获取构建依赖并重试打包")
                self.run(command)
        else:
            self.run(command)
        runtime_cache = ROOT / f"output/macos-app/cache/python-{self.arch}.tar.gz"
        runtime_cache.parent.mkdir(parents=True, exist_ok=True)
        result_dir = self.work / "macos"
        self.run(
            [
                python,
                str(ROOT / "scripts/build_macos_app.py"),
                "--bundle-dir",
                str(stage),
                "--output-dir",
                str(result_dir),
                "--python-archive",
                str(runtime_cache),
                "--arch",
                self.arch,
                "--source-installer",
            ]
        )
        candidate = result_dir / published.name
        publish_package(stage, cache, candidate, published, self.work)
        private_write(
            state_file, json.dumps({"signature": signature, "sha256": digest(published)}, indent=2)
        )
        return {"path": str(published), "sha256": digest(published), "reused": False}

    def apply(self, *, rebuild: bool = False) -> dict:
        before = self.preflight()
        if self.check:
            if self.missing_routes:
                raise OperatorError("私网 HTTPS 入口缺失；运行维护入口可恢复指定端口。")
            self.health()
            return {
                "mode": "check",
                "healthy": True,
                "live_preserved": self.live,
                "counts": before["counts"],
            }
        python = self.build_python()
        self.backup(before)
        connection_file = self.directory / "operator.json"
        if not connection_file.exists():
            private_write(connection_file, json.dumps(self.connection, indent=2))
        if rebuild:
            self.rebuild()
        self.progress("启动或恢复现有服务，保留数据库和分享设置")
        self.run([*self.compose, "up", "-d", "--wait", "--wait-timeout", "120"])
        for port, proxy in self.missing_routes:
            self.run([self.tailscale, "serve", "--bg", f"--https={port}", proxy])
        serve_after = json.loads(
            self.run([self.tailscale, "serve", "status", "--json"], private=True)
        )
        for section in ("TCP", "Web", "AllowFunnel"):
            if any(
                serve_after.get(section, {}).get(key) != value
                for key, value in self.serve_before.get(section, {}).items()
            ):
                raise OperatorError("现有 Tailscale 入口发生变化，请检查备份配置。")
        self.health()
        if (
            str(
                self.config()["services"]["api"]["environment"].get(
                    "EXPERIMENT_LIVE_ENABLED", "false"
                )
            ).lower()
            != self.live
        ):
            raise OperatorError("真实执行开关发生变化，请检查运营配置。")
        installer = self.package(python)
        report = {
            "mode": "apply",
            "healthy": True,
            "live_preserved": self.live,
            "counts_before": before["counts"],
            "installer": installer,
            "backup": str(self.work),
        }
        private_write(self.work / "result.json", json.dumps(report, indent=2))
        return report


def publish_package(
    stage: Path, destination: Path, candidate: Path, published: Path, backup: Path
) -> None:
    """Publish only a complete candidate; restore both old outputs if replacement fails."""
    published.parent.mkdir(parents=True, exist_ok=True)
    previous = backup / "previous-worker-bundle"
    previous_zip = backup / "previous-installer.zip"
    if published.exists():
        shutil.copyfile(published, previous_zip)
        previous_zip.chmod(0o600)
    if destination.exists():
        destination.rename(previous)
    try:
        stage.rename(destination)
        temporary = published.with_suffix(".new")
        shutil.copyfile(candidate, temporary)
        temporary.chmod(0o600)
        temporary.replace(published)
    except Exception:
        if destination.exists():
            shutil.rmtree(destination)
        if previous.exists():
            previous.rename(destination)
        if previous_zip.exists():
            shutil.copyfile(previous_zip, published)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-dir", type=Path, default=ROOT.parent / "spareagent-private")
    parser.add_argument(
        "--check", action="store_true", help="read-only checks; do not deploy or package"
    )
    parser.add_argument(
        "--rebuild", action="store_true", help="build API/web images from current source first"
    )
    parser.add_argument("--arch", choices=("arm64", "x86_64"), default=platform.machine())
    args = parser.parse_args()
    if args.check and args.rebuild:
        parser.error("--check cannot be combined with --rebuild")
    operator = None
    try:
        operator = Operator(args.private_dir, check=args.check, arch=args.arch)
        if args.check:
            report = operator.apply()
        else:
            lock_path = args.private_dir / "private/operator.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with lock_path.open("a") as lock:
                lock_path.chmod(0o600)
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise OperatorError("已有维护操作正在运行，请等它完成。") from None
                operator.start_run()
                report = operator.apply(rebuild=args.rebuild)
        print("完成：服务健康。", flush=True)
        if report.get("installer"):
            print(f"私有安装包：{report['installer']['path']}\n备份和日志：{report['backup']}")
        return 0
    except (OperatorError, OSError, ValueError, KeyError) as exc:
        step = operator.step if operator else "检查运行条件"
        reason = str(exc) if isinstance(exc, OperatorError) else type(exc).__name__
        print(f"停止于「{step}」：{reason}", file=sys.stderr)
        if operator and operator.work:
            private_write(
                operator.work / "failure.json",
                json.dumps({"step": step, "error_type": type(exc).__name__}, indent=2),
            )
            print(f"检查日志：{operator.work}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
