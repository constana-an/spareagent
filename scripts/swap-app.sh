#!/bin/bash
# In-app update (D-103), run by the menu-bar App from a temporary copy as it quits:
# wait for that App to exit, put the staged App in its place, roll back if the result
# does not verify, then open whichever App is installed. Never touches Gatekeeper.
set -u
pid="$1" staged="$2" target="$3"
for _ in $(seq 1 600); do
  kill -0 "$pid" 2>/dev/null || break
  sleep 0.5
done
kill -0 "$pid" 2>/dev/null && exit 1 # still running after five minutes: change nothing
reopen() { [[ "${SPAREAGENT_SWAP_NO_OPEN:-0}" == 1 ]] || /usr/bin/open "$target"; }
if [[ ! -d "$staged" || ! -d "$target" || -L "$target" ]]; then
  reopen
  exit 1
fi
backup="$(dirname "$target")/.SpareAgent-previous-$$.app"
if ! mv "$target" "$backup"; then
  reopen
  exit 1
fi
if mv "$staged" "$target" && /usr/bin/codesign --verify --strict "$target"; then
  rm -rf "$backup"
  result=0
else
  rm -rf "$target"
  mv "$backup" "$target"
  result=1
fi
reopen
exit "$result"
