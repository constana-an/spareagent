#!/bin/bash
# SpareAgent 一键提供算力 / one-click sharing. Double-click in Finder (first time: right-click
# → Open). Needs no Homebrew, Python or Node: on first launch it downloads the pinned official
# Python build listed in runtime.json, checks its SHA-256, installs the worker from the wheels
# next to this file (checked against SHA256SUMS), then opens the control page. Closing this
# window takes the device offline.
set -euo pipefail
umask 077
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here"
# Finder starts .command files with a minimal PATH; add the usual tool locations.
PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
for d in "$HOME"/.nvm/versions/node/*/bin; do [[ -d "$d" ]] && PATH="$d:$PATH"; done
export PATH

pause() { [[ "${SPAREAGENT_NATIVE_APP:-0}" == 1 ]] || read -r -p "按回车关闭 / Press Enter to close " _ || true; }
fail() { printf '\n%s\n' "$*" >&2; pause; exit 1; }
pin() { plutil -extract "$1" raw -o - runtime.json 2>/dev/null; }

[[ -f SHA256SUMS && -f bundle.json && -f runtime.json ]] ||
  fail "安装包不完整（需要 SHA256SUMS、bundle.json、runtime.json）。Incomplete package."
# Every file listed in SHA256SUMS (worker wheel, dependency wheels, runtime.json) must match.
shasum -a 256 -c SHA256SUMS --status 2>/dev/null ||
  fail "安装包校验失败，请向运营者重新获取。Checksum mismatch: ask the operator again."

case "$(uname -m)" in
  arm64) arch=arm64 ;;
  x86_64) arch=x86_64 ;;
  *) fail "不支持这台电脑的处理器。Unsupported CPU: $(uname -m)" ;;
esac
py_url="$(pin "python.assets.$arch.url")"
py_sha="$(pin "python.assets.$arch.sha256")"
py_bundle="python-$arch.tar.gz"
[[ ! -f "python-$arch.zip" ]] || py_bundle="python-$arch.zip"
if [[ -f "$py_bundle" ]]; then
  bundled_sha="$(pin "python.assets.$arch.bundled_sha256" || true)"
  [[ -z "$bundled_sha" ]] || py_sha="$bundled_sha"
fi
[[ -n "$py_url" && -n "$py_sha" ]] || fail "安装包缺少运行环境信息。runtime.json is incomplete."

base="${SPAREAGENT_APP_HOME:-$HOME/Library/Application Support/SpareAgent}"
pyhome="$base/python-${py_sha:0:16}"
mkdir -p "$base"
tmp=""; backup=""; installing=0
cleanup() {
  local result=$?
  if [[ "$installing" == 1 && "$result" != 0 ]]; then
    rm -rf "$base/worker"
    [[ -z "$backup" ]] || mv "$backup" "$base/worker"
  fi
  [[ -z "$tmp" ]] || rm -rf "$tmp"
}
trap cleanup EXIT
if [[ ! -x "$pyhome/bin/python3" ]]; then
  tmp="$(mktemp -d "$base/.python-XXXXXX")"
  if [[ -f "$py_bundle" ]]; then
    echo "正在准备内置运行环境… / Preparing the bundled runtime…"
    cp "$py_bundle" "$tmp/python.archive"
  elif [[ "${SPAREAGENT_NATIVE_APP:-0}" == 1 ]]; then
    fail "App 缺少这台 Mac 的运行环境，请重新获取对应处理器的安装包。Bundled runtime missing."
  else
    echo "首次运行：正在下载运行环境（约 25 MB）… / First run: downloading the runtime (~25 MB)…"
    curl -fL --proto '=https' --tlsv1.2 --retry 3 --connect-timeout 20 -o "$tmp/python.archive" "$py_url" ||
      fail "下载运行环境失败，请检查网络后再双击本文件。Download failed: check the network and retry."
  fi
  [[ "$(shasum -a 256 "$tmp/python.archive" | cut -d ' ' -f 1)" == "$py_sha" ]] ||
    fail "运行环境校验失败，已停止。Runtime checksum mismatch; stopped."
  if [[ "$py_bundle" == *.zip && -f "$py_bundle" ]]; then
    ditto -x -k "$tmp/python.archive" "$tmp"
  else
    tar -xzf "$tmp/python.archive" -C "$tmp"
  fi
  [[ -x "$tmp/python/bin/python3" ]] || fail "运行环境格式不正确。Unexpected runtime layout."
  rm -rf "$pyhome"
  mv "$tmp/python" "$pyhome"
  rm -rf "$tmp"
  tmp=""
fi

wheel="$(ls spareagent_worker-*-py3-none-any.whl 2>/dev/null | head -1)"
[[ -n "$wheel" ]] || fail "安装包缺少 Worker。Worker wheel missing."
stamp="$(shasum -a 256 SHA256SUMS | cut -d ' ' -f 1)-${py_sha:0:16}"
venv="$base/worker"
if [[ "$(cat "$venv/.package-sha256" 2>/dev/null || true)" != "$stamp" ]]; then
  echo "正在安装 SpareAgent Worker… / Installing the worker…"
  if [[ -d "$venv" ]]; then
    backup="$(mktemp -d "$base/.previous-worker-XXXXXX")"
    rmdir "$backup"
    mv "$venv" "$backup"
  fi
  installing=1
  "$pyhome/bin/python3" -m venv "$venv"
  # Offline: only the wheels shipped (and checksummed) in this package.
  "$venv/bin/python" -m pip install --disable-pip-version-check -q --no-index \
    --find-links "$here/wheels" --find-links "$here" "$wheel"
  printf '%s' "$stamp" > "$venv/.package-sha256"
  installing=0
  [[ -z "$backup" ]] || rm -rf "$backup"
  backup=""
fi

echo "正在打开控制页面… / Opening the control page…"
if [[ "${SPAREAGENT_NATIVE_APP:-0}" == 1 ]]; then
  exec "$venv/bin/spareagent-worker" app --bundle "$here/bundle.json" --no-browser
else
  exec "$venv/bin/spareagent-worker" app --bundle "$here/bundle.json"
fi
