#!/bin/bash
# One operator entry; the saved private deployment remains the source of truth.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:/opt/miniconda3/bin:$PATH"
python="${SPAREAGENT_OPERATOR_PYTHON:-$(command -v python3 || true)}"
if [[ -z "$python" ]] || ! "$python" -c 'import sys; assert sys.version_info >= (3,12)' 2>/dev/null; then
  echo "需要 Python 3.12 或更新版本，请安装后重试。" >&2
  result=1
else
  "$python" "$here/operator.py" "$@"
  result=$?
fi
if [[ -t 0 ]]; then read -r -p "按回车关闭 / Press Enter to close " _ || true; fi
exit "$result"
