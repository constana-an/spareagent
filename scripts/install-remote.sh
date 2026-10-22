#!/bin/bash
# One-line install for owners: curl -fsSL <link>/install.sh | bash
# Downloads the operator's prebuilt, ad-hoc signed App over the private HTTPS link and
# checks its SHA-256. Files fetched with curl carry no quarantine attribute, so no local
# compile or Apple command line tools are needed. This script never removes quarantine
# attributes and never changes Gatekeeper settings.
# scripts/owner_install_link.py fills in the download URL, checksum and CPU below.
set -euo pipefail

main() {
  umask 077
  local zip_url="@@ZIP_URL@@" zip_sha256="@@ZIP_SHA256@@" want_arch="@@ARCH@@"
  fail() { printf '\n%s\n' "$*" >&2; exit 1; }

  [[ "$(uname -s)" == Darwin ]] || fail "需要 macOS 13 或更新版本。Requires macOS 13 or later."
  [[ "$(sw_vers -productVersion | cut -d . -f 1)" -ge 13 ]] ||
    fail "需要 macOS 13 或更新版本。Requires macOS 13 or later."
  local arch
  arch="$(uname -m)"
  [[ "$arch" == "$want_arch" ]] ||
    fail "这个安装链接是给 ${want_arch} 处理器的，你的 Mac 是 ${arch}。请向运营者要对应的链接。Wrong CPU link."

  local applications="${SPAREAGENT_INSTALL_DIR:-$HOME/Applications}"
  target="$applications/SpareAgent.app" # global: read by the EXIT trap
  local state="${SPAREAGENT_WORKER_HOME:-$HOME/.spareagent-worker}"
  if [[ -f "$state/worker.pid" ]]; then
    local pid
    pid="$(cat "$state/worker.pid")"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      fail "SpareAgent 接单程序仍在运行。请从菜单栏退出后重试。Quit SpareAgent before updating."
    fi
  fi
  [[ ! -L "$target" ]] || fail "安装位置是符号链接，请选择普通 Applications 文件夹。App path is a symlink."
  if [[ -e "$target" ]]; then
    [[ "$(/usr/libexec/PlistBuddy -c 'Print CFBundleIdentifier' "$target/Contents/Info.plist" 2>/dev/null)" == org.spareagent.desktop ]] ||
      fail "安装位置已有其他内容，请先移开；没有覆盖。Existing destination is not SpareAgent."
    if /usr/sbin/lsof -t "$target/Contents/MacOS/SpareAgent" >/dev/null 2>&1; then
      fail "SpareAgent App 仍在运行，请先从菜单栏退出后重试。Quit SpareAgent before updating."
    fi
  fi

  mkdir -p "$applications"
  work="$(mktemp -d "$applications/.spareagent-install-XXXXXX")"
  backup="$work/previous.app"
  installing=0
  cleanup() {
    local result=$?
    if [[ "$installing" == 1 && "$result" != 0 ]]; then
      rm -rf "$target"
      [[ ! -d "$backup" ]] || mv "$backup" "$target"
    fi
    rm -rf "$work"
  }
  trap cleanup EXIT

  echo "正在下载 SpareAgent（约 25 MB）…"
  /usr/bin/curl -fL --progress-bar --proto =https --tlsv1.2 --retry 3 -o "$work/app.zip" "$zip_url" ||
    fail "下载没有完成。请确认 Tailscale 已连接，链接没有被撤销，然后重试。Download failed."
  [[ "$(/usr/bin/shasum -a 256 "$work/app.zip" | cut -d ' ' -f 1)" == "$zip_sha256" ]] ||
    fail "下载的文件校验失败，原有 App 未改动。请重新运行这行命令。Checksum mismatch."
  /usr/bin/ditto -x -k "$work/app.zip" "$work/unpacked" ||
    fail "解压失败，原有 App 未改动。Could not unpack."
  local app="$work/unpacked/SpareAgent.app"
  [[ "$(/usr/libexec/PlistBuddy -c 'Print CFBundleIdentifier' "$app/Contents/Info.plist" 2>/dev/null)" == org.spareagent.desktop ]] ||
    fail "下载内容不是 SpareAgent，原有 App 未改动。Unexpected download."
  /usr/bin/codesign --verify --strict "$app" ||
    fail "App 签名校验失败，原有 App 未改动。Signature check failed."

  [[ ! -e "$target" ]] || mv "$target" "$backup"
  installing=1
  mv "$app" "$target"
  installing=0
  printf '\n已安装：%s\n以后双击 SpareAgent.app 即可启动。启动后默认离线，需要你确认上线。\n' "$target"
  if [[ "${SPAREAGENT_INSTALL_NO_OPEN:-0}" != 1 ]]; then
    /usr/bin/open "$target" || echo "请在个人 Applications 文件夹中双击 SpareAgent.app。"
  fi
}

# Everything runs from main, so a download cut short by the network executes nothing.
main "$@"
