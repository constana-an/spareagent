#!/bin/bash
# Run a reviewed local script with the operator's wheel and independently checked SHA-256.
set -euo pipefail
umask 077
if [[ "$(uname -s)" != Darwin ]]; then
  echo 'This setup is for macOS. See the worker runbook for other systems.' >&2; exit 2
fi
if [[ $# -ne 2 || ! "$2" =~ ^[0-9a-fA-F]{64}$ ]]; then
  echo 'Usage: bash setup-worker-macos.sh /path/spareagent_worker-0.10.0-py3-none-any.whl SHA256' >&2; exit 2
fi
wheel="$1"
expected="$2"
[[ -f "$wheel" && "$wheel" == *spareagent_worker-0.10.0-py3-none-any.whl ]] || { echo 'Expected the operator-supplied 0.10.0 worker wheel.' >&2; exit 2; }
actual="$(shasum -a 256 "$wheel" | cut -d ' ' -f 1)"
[[ "$actual" == "$expected" ]] || { echo 'Wheel SHA-256 mismatch. Stop and contact the operator.' >&2; exit 1; }
python_bin="${SPAREAGENT_PYTHON:-python3.12}"
"$python_bin" -c 'import sys; assert sys.version_info >= (3,12)' || { echo 'Python 3.12+ required (for example brew install python@3.12).' >&2; exit 2; }
command -v git >/dev/null || { echo 'Install Apple command line tools: xcode-select --install' >&2; exit 2; }
if ! command -v "${SPAREAGENT_CODEX_BIN:-codex}" >/dev/null; then
  echo 'Install the official Codex CLI first (brew install codex), then rerun. Setup never runs inference.' >&2; exit 2
fi
install_root="${SPAREAGENT_WORKER_INSTALL_DIR:-$HOME/.local/share/spareagent-worker}"
if [[ -e "$install_root" ]]; then
  echo "Install directory already exists: $install_root. Keep it or remove it explicitly before reinstalling." >&2; exit 2
fi
"$python_bin" -m venv "$install_root"
"$install_root/bin/python" -m pip install --disable-pip-version-check "$wheel"
"$install_root/bin/spareagent-worker" --help >/dev/null
printf '\nInstalled Worker only. No server, database, Docker, login or LIVE activation.\n'
printf 'Add to your shell PATH: export PATH="%s/bin:$PATH"\n' "$install_root"
printf 'Next: codex login (your own ChatGPT Plus), then follow the private enrollment instructions.\n'
printf 'Run: %s/bin/spareagent-worker pilot-check\n' "$install_root"
printf 'pilot-check never runs inference; exit 1 = blocked, 2 = manual attestations required.\n'
