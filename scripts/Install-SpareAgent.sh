#!/bin/bash
# Compile the native launcher locally; all Worker resources come from the private kit.
set -euo pipefail
umask 077
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here"
fail() { printf '\n%s\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == Darwin ]] || fail "需要 macOS 13 或更新版本。Requires macOS 13 or later."
[[ -f INSTALL_SHA256SUMS ]] || fail "安装包不完整，请重新获取。Incomplete installer."
shasum -a 256 -c INSTALL_SHA256SUMS --status 2>/dev/null ||
  fail "安装包校验失败，请重新获取；原有 App 未改动。Installer checksum mismatch."
arch="$(uname -m)"
[[ "$(cat ARCH)" == "$arch" ]] || fail "安装包与处理器不匹配，请获取 $arch 版本。Wrong CPU package."
[[ "$(sw_vers -productVersion | cut -d . -f 1)" -ge 13 ]] ||
  fail "需要 macOS 13 或更新版本。Requires macOS 13 or later."

if ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
  echo "首次需要 Apple 命令行工具。请在系统窗口中完成安装，再运行本脚本。"
  /usr/bin/xcode-select --install || true
  exit 2
fi
/usr/bin/xcrun --find swiftc >/dev/null 2>&1 ||
  fail "Apple 编译工具不可用。请完成命令行工具安装后重试。Swift compiler unavailable."

applications="${SPAREAGENT_INSTALL_DIR:-$HOME/Applications}"
target="$applications/SpareAgent.app"
state="${SPAREAGENT_WORKER_HOME:-$HOME/.spareagent-worker}"
if [[ -f "$state/worker.pid" ]]; then
  pid="$(cat "$state/worker.pid")"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    fail "SpareAgent 接单程序仍在运行。请从菜单栏退出，或关闭旧启动程序后重试。Quit SpareAgent before updating."
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
app="$work/SpareAgent.app"
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
mkdir -p "$app/Contents/MacOS"
cp Info.plist "$app/Contents/Info.plist"
cp -R Resources "$app/Contents/Resources"
chmod 600 "$app/Contents/Resources/bundle.json"
echo "正在这台 Mac 上生成 SpareAgent App，首次可能需要几分钟…"
/usr/bin/xcrun swiftc -swift-version 5 -O -target "$arch-apple-macos13.0" \
  -module-cache-path "$work/swift-cache" SpareAgent.swift -o "$app/Contents/MacOS/SpareAgent" ||
  fail "编译没有完成，原有 App 未改动。请检查 Apple 命令行工具后重试。Compilation failed."
"$app/Contents/MacOS/SpareAgent" --self-test
printf 'source_sha256=%s\narchitecture=%s\n' "$(shasum -a 256 SpareAgent.swift | cut -d ' ' -f 1)" "$arch" \
  > "$app/Contents/Resources/LOCAL_BUILD.txt"
# Remove only Finder metadata that codesign rejects, never quarantine attributes.
for attribute in com.apple.FinderInfo com.apple.ResourceFork; do
  /usr/bin/xattr -dr "$attribute" "$app" 2>/dev/null || true
done
/usr/bin/codesign --force --sign - "$app"
/usr/bin/codesign --verify --strict "$app"
[[ ! -e "$target" ]] || mv "$target" "$backup"
installing=1
mv "$app" "$target"
installing=0
printf '\n已安装：%s\n以后双击 SpareAgent.app 即可启动。启动后默认离线，需要你确认上线。\n' "$target"
if [[ "${SPAREAGENT_INSTALL_NO_OPEN:-0}" != 1 ]]; then
  /usr/bin/open "$target" || echo "请在个人 Applications 文件夹中双击 SpareAgent.app。"
fi
