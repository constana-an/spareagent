#!/bin/bash
# SpareAgent 一键提供算力 / one-click sharing. Double-click in Finder (first time: right-click
# → Open). Installs the worker from the wheel next to this file after checking its SHA-256,
# then opens the control page. Closing this window takes the device offline.
set -euo pipefail
umask 077
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here"
# Finder starts .command files with a minimal PATH; add the usual tool locations.
PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
for d in "$HOME"/.nvm/versions/node/*/bin; do [[ -d "$d" ]] && PATH="$d:$PATH"; done
export PATH

pause() { read -r -p "按回车关闭 / Press Enter to close " _ || true; }
fail() { printf '\n%s\n' "$*" >&2; pause; exit 1; }

wheel="$(ls spareagent_worker-*-py3-none-any.whl 2>/dev/null | head -1)"
[[ -n "$wheel" && -f SHA256SUMS && -f bundle.json ]] ||
  fail "安装包不完整（需要 wheel、SHA256SUMS、bundle.json）。Incomplete package."
expected="$(awk -v w="$wheel" '$2 == w {print $1}' SHA256SUMS)"
actual="$(shasum -a 256 "$wheel" | cut -d ' ' -f 1)"
[[ -n "$expected" && "$actual" == "$expected" ]] ||
  fail "安装包校验失败，请向运营者重新获取。Checksum mismatch: ask the operator again."

root="${SPAREAGENT_WORKER_INSTALL_DIR:-$HOME/.local/share/spareagent-worker}"
if [[ "$(cat "$root/.wheel-sha256" 2>/dev/null || true)" != "$actual" ]]; then
  echo "正在安装 SpareAgent Worker… / Installing the worker…"
  py=""
  for c in python3.13 python3.12 python3; do
    if command -v "$c" >/dev/null &&
      "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
      py="$(command -v "$c")"; break
    fi
  done
  if [[ -z "$py" ]]; then
    if command -v brew >/dev/null; then
      echo "正在安装 Python 3.12… / Installing Python 3.12…"
      brew install python@3.12
      py="$(brew --prefix)/bin/python3.12"
    else
      open "https://www.python.org/downloads/macos/"
      fail "需要 Python 3.12 或更新版本：请在打开的网页安装后，再双击本文件。Install Python 3.12+, then double-click again."
    fi
  fi
  if ! xcode-select -p >/dev/null 2>&1; then
    xcode-select --install >/dev/null 2>&1 || true
    fail "请在弹出的窗口安装 Apple 命令行工具，完成后再双击本文件。Install the Apple command line tools, then double-click again."
  fi
  [[ -x "$root/bin/python" ]] || "$py" -m venv "$root"
  "$root/bin/python" -m pip install --disable-pip-version-check -q --force-reinstall "$wheel"
  printf '%s' "$actual" > "$root/.wheel-sha256"
fi

echo "正在打开控制页面… / Opening the control page…"
exec "$root/bin/spareagent-worker" app --bundle "$here/bundle.json"
