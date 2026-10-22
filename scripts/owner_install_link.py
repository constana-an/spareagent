"""One-line install links for owners: create, list and revoke.

Each link is a folder with an unguessable name under `private/install/` in the private
deployment directory, served read-only by the TLS proxy at `<api_url>/install/`. It holds
the prebuilt, ad-hoc signed App ZIP (which contains the signup code) and an `install.sh`
bound to that ZIP's SHA-256. Deleting the folder revokes the link. Expired links are
removed whenever this tool runs.

    python scripts/owner_install_link.py create --label 小明
    python scripts/owner_install_link.py list
    python scripts/owner_install_link.py revoke 小明

`publish-update` puts the same prebuilt App into `private/app-release/` (served to
registered devices by the API, D-103) so installed Apps offer it as an in-app update:

    python scripts/owner_install_link.py publish-update
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import secrets
import shutil
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "scripts/install-remote.sh"
META = "link.json"
RELEASE = "release.json"
APP_PLIST = "SpareAgent.app/Contents/Info.plist"


class LinkError(Exception):
    pass


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def check_base(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise LinkError("API 地址必须是不带路径的 https 地址，例如 operator.json 里的 api_url。")
    return url.rstrip("/")


def check_app_zip(path: Path) -> None:
    if not path.is_file():
        raise LinkError(
            f"找不到预编译 App：{path}。先运行 build_macos_app.py（不加 --source-installer）。"
        )
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    if "SpareAgent.app/Contents/MacOS/SpareAgent" not in names:
        raise LinkError(
            "这个 ZIP 里没有预编译的 SpareAgent.app；本地编译安装包不能用于一行命令安装。"
        )


def app_version(path: Path) -> tuple[str, int]:
    """CFBundleShortVersionString and CFBundleVersion of the App inside the ZIP."""
    with zipfile.ZipFile(path) as archive:
        info = plistlib.loads(archive.read(APP_PLIST))
    if info.get("CFBundleIdentifier") != "org.spareagent.desktop":
        raise LinkError("这个 ZIP 里的 App 不是 SpareAgent。")
    return str(info["CFBundleShortVersionString"]), int(info["CFBundleVersion"])


def publish_update(release_dir: Path, app_zip: Path, arch: str) -> dict:
    """Offer `app_zip` as the in-app update for `arch`. Another CPU's entry is kept only
    when it is the same build, so one release.json never mixes versions."""
    check_app_zip(app_zip)
    version, build = app_version(app_zip)
    release_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    manifest = release_dir / RELEASE
    current = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    if current.get("build", 0) > build:
        newer = current["version"]
        raise LinkError(f"已发布的版本 {newer} 比这个 App（{version}）更新，没有改动。")
    apps = current.get("apps", {}) if current.get("build") == build else {}
    name = f"SpareAgent-mac-{arch}.zip"
    staging = release_dir / f".{name}"
    shutil.copyfile(app_zip, staging)
    staging.chmod(0o644)
    os.replace(staging, release_dir / name)
    apps[arch] = {"file": name, "sha256": sha256(release_dir / name)}
    release = {"version": version, "build": build, "apps": apps}
    temp = release_dir / f".{RELEASE}"
    temp.write_text(json.dumps(release, indent=2), encoding="utf-8")
    temp.chmod(0o644)
    os.replace(temp, manifest)
    return release


def read_links(root: Path) -> list[dict]:
    links = []
    for meta in sorted(root.glob(f"*/{META}")):
        links.append({**json.loads(meta.read_text(encoding="utf-8")), "dir": meta.parent})
    return links


def prune(root: Path, now: datetime) -> list[dict]:
    removed = []
    for link in read_links(root):
        if datetime.fromisoformat(link["expires_at"]) <= now:
            shutil.rmtree(link["dir"])
            removed.append(link)
    return removed


def create(
    root: Path, base_url: str, app_zip: Path, arch: str, label: str, days: int, now: datetime
) -> dict:
    if not label.strip() or len(label) > 60:
        raise LinkError("请用 --label 写明这个链接给谁（不超过 60 个字）。")
    if not 1 <= days <= 30:
        raise LinkError("有效天数必须在 1 到 30 之间。")
    base = check_base(base_url)
    check_app_zip(app_zip)
    if any(link["label"] == label for link in read_links(root)):
        raise LinkError(f"已有给「{label}」的链接。先撤销，或换一个名字。")
    token = secrets.token_urlsafe(24)
    zip_name = f"SpareAgent-{arch}.zip"
    digest = sha256(app_zip)
    url = f"{base}/install/{token}"
    script = TEMPLATE.read_text(encoding="utf-8")
    for key, value in (
        ("@@ZIP_URL@@", f"{url}/{zip_name}"),
        ("@@ZIP_SHA256@@", digest),
        ("@@ARCH@@", arch),
    ):
        script = script.replace(key, value)
    # Staged under a dot name the proxy cannot be asked for, then renamed into place,
    # so a link is never served half-written.
    # Readable by the proxy's worker user; private/ above it stays 0700 on this Mac.
    root.mkdir(mode=0o755, parents=True, exist_ok=True)
    staging = root / f".{token}"
    staging.mkdir(mode=0o755)
    shutil.copyfile(app_zip, staging / zip_name)
    (staging / "install.sh").write_text(script, encoding="utf-8")
    link = {
        "label": label,
        "arch": arch,
        "zip_sha256": digest,
        "created_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(days=days)).isoformat(timespec="seconds"),
        "command": f"curl -fsSL {url}/install.sh | bash",
    }
    (staging / META).write_text(json.dumps(link, ensure_ascii=False, indent=2), encoding="utf-8")
    # The proxy's worker processes read these; the folder name is the secret.
    for path in staging.iterdir():
        path.chmod(0o644)
    staging.rename(root / token)
    return link


def revoke(root: Path, which: str) -> list[dict]:
    matches = [link for link in read_links(root) if which in (link["label"], link["dir"].name)]
    for link in matches:
        shutil.rmtree(link["dir"])
    return matches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--private-dir", type=Path, default=ROOT.parent / "spareagent-private")
    sub = parser.add_subparsers(dest="action", required=True)
    make = sub.add_parser("create", help="make a link for one owner")
    make.add_argument("--label", required=True, help="who the link is for")
    make.add_argument("--days", type=int, default=7, help="valid for this many days (1-30)")
    make.add_argument("--arch", choices=("arm64", "x86_64"), default=platform.machine())
    make.add_argument("--app-zip", type=Path, help="prebuilt App ZIP from build_macos_app.py")
    make.add_argument("--api-url", help="defaults to api_url in operator.json")
    sub.add_parser("list", help="show links that are still valid")
    publish = sub.add_parser("publish-update", help="offer the prebuilt App as an in-app update")
    publish.add_argument("--arch", choices=("arm64", "x86_64"), default=platform.machine())
    publish.add_argument("--app-zip", type=Path, help="prebuilt App ZIP from build_macos_app.py")
    gone = sub.add_parser("revoke", help="delete a link by label or folder name")
    gone.add_argument("which")
    args = parser.parse_args(argv)

    root = args.private_dir / "private/install"
    now = datetime.now(UTC)
    try:
        for link in prune(root, now) if root.is_dir() else []:
            print(f"已过期并删除：{link['label']}")
        if args.action == "create":
            api_url = (
                args.api_url
                or json.loads((args.private_dir / "operator.json").read_text(encoding="utf-8"))[
                    "api_url"
                ]
            )
            app_zip = args.app_zip or ROOT / f"output/macos-app/SpareAgent-mac-{args.arch}.zip"
            link = create(root, api_url, app_zip, args.arch, args.label, args.days, now)
            print(
                f"给「{link['label']}」的安装命令"
                f"（{link['expires_at'][:10]} 前有效，含注册码，请私下发送）：\n\n"
                f"{link['command']}\n\n"
                "对方需要先连上 Tailscale，然后在「终端」里粘贴这一行并回车。装好后可以撤销："
                f"python scripts/owner_install_link.py revoke {link['label']}"
            )
        elif args.action == "publish-update":
            app_zip = args.app_zip or ROOT / f"output/macos-app/SpareAgent-mac-{args.arch}.zip"
            release = publish_update(args.private_dir / "private/app-release", app_zip, args.arch)
            print(
                f"已发布 App {release['version']}（build {release['build']}，"
                f"{'、'.join(sorted(release['apps']))}）。已装旧版的提供者会在菜单栏看到「更新」。"
            )
        elif args.action == "list":
            links = read_links(root) if root.is_dir() else []
            for link in links:
                expires = link["expires_at"][:16]
                print(f"{link['label']}\t{link['arch']}\t到期 {expires}\t{link['command']}")
            if not links:
                print("没有有效的安装链接。")
        else:
            removed = revoke(root, args.which) if root.is_dir() else []
            if not removed:
                raise LinkError(f"没有找到「{args.which}」。")
            for link in removed:
                print(f"已撤销：{link['label']}")
    except LinkError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
