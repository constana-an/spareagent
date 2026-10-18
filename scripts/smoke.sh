#!/usr/bin/env bash
# End-to-end smoke test against the running compose stack. Uses only the MOCK provider:
# no model credentials, no paid calls. Requires curl and python3 on the host.
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
CONSUMER="${SMOKE_CONSUMER:-11111111-1111-4111-8111-111111111111}"
OTHER_USER="${SMOKE_OTHER_USER:-22222222-2222-4222-8222-222222222222}"
POLL_SECONDS="${SMOKE_POLL_SECONDS:-30}"
SEED="${SMOKE_SEED:-1}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass() { printf '  PASS %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1" >&2; exit 1; }
json() { python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2], {'d': d}))" "$1" "$2"; }

# req METHOD PATH OUTFILE [curl args...] -> prints HTTP status
req() {
  local method="$1" path="$2" out="$3"; shift 3
  curl -sS -o "$out" -w '%{http_code}' -X "$method" "$BASE_URL$path" -H "X-Dev-User-ID: $CONSUMER" "$@"
}

echo "SpareAgent smoke test -> $BASE_URL"

if [[ "$SEED" == "1" ]]; then
  docker compose exec -T api python scripts/seed_dev.py >/dev/null && pass "seed dev fixtures (repeatable)"
fi

[[ "$(curl -sS -o /dev/null -w '%{http_code}' "$BASE_URL/healthz")" == 200 ]] && pass "GET /healthz 200" || fail "healthz"
[[ "$(curl -sS -o "$TMP/ready.json" -w '%{http_code}' "$BASE_URL/readyz")" == 200 ]] && pass "GET /readyz 200 (db+redis)" || fail "readyz: $(cat "$TMP/ready.json")"

KEY="smoke-$(date +%s)-$RANDOM"
BODY='{"task_type":"WRITE_TESTS","repository_url":"https://github.com/example/public-fixture","commit_sha":"0123456789abcdef0123456789abcdef01234567","prompt":"Write two tests for the demo fixture","max_budget_cents":500,"max_duration_seconds":120}'

code=$(req POST /v1/tasks "$TMP/task.json" -H 'Content-Type: application/json' -H "Idempotency-Key: $KEY" -d "$BODY")
[[ "$code" == 202 ]] || fail "create task -> $code $(cat "$TMP/task.json")"
TASK_ID=$(json "$TMP/task.json" "d['id']")
pass "POST /v1/tasks 202 id=$TASK_ID status=$(json "$TMP/task.json" "d['status']")"

code=$(req POST /v1/tasks "$TMP/replay.json" -H 'Content-Type: application/json' -H "Idempotency-Key: $KEY" -d "$BODY")
[[ "$code" == 202 && "$(json "$TMP/replay.json" "d['id']")" == "$TASK_ID" ]] && pass "idempotent replay returns same id" || fail "replay"

code=$(req POST /v1/tasks "$TMP/conflict.json" -H 'Content-Type: application/json' -H "Idempotency-Key: $KEY" -d "${BODY/two tests/three tests}")
[[ "$code" == 409 && "$(json "$TMP/conflict.json" "d['error']['code']")" == IDEMPOTENCY_CONFLICT ]] && pass "same key + different body 409" || fail "idempotency conflict -> $code"

code=$(curl -sS -o /dev/null -w '%{http_code}' "$BASE_URL/v1/tasks/$TASK_ID" -H "X-Dev-User-ID: $OTHER_USER")
[[ "$code" == 404 ]] && pass "cross-user GET is 404" || fail "cross-user GET -> $code"

status=""
for _ in $(seq 1 $((POLL_SECONDS * 2))); do
  req GET "/v1/tasks/$TASK_ID" "$TMP/poll.json" >/dev/null
  status=$(json "$TMP/poll.json" "d['status']")
  case "$status" in SUCCEEDED|FAILED|CANCELLED|TIMED_OUT) break ;; esac
  sleep 0.5
done
[[ "$status" == SUCCEEDED ]] && pass "task reached SUCCEEDED within ${POLL_SECONDS}s" || fail "task ended as '$status'"
pass "result_summary: $(json "$TMP/poll.json" "d['result_summary']")"

curl -sS -N --max-time 10 "$BASE_URL/v1/tasks/$TASK_ID/events" -H "X-Dev-User-ID: $CONSUMER" > "$TMP/events.txt"
TYPES=$(grep '^event: ' "$TMP/events.txt" | sed 's/^event: //' | uniq | paste -sd, -)
EXPECTED="task.created,task.queued,task.reserved,task.started,task.progress,task.verifying,task.succeeded"
[[ "$TYPES" == "$EXPECTED" ]] && pass "SSE ordered events: $TYPES" || fail "events were: $TYPES"
SEQS=$(grep '^id: ' "$TMP/events.txt" | sed 's/^id: //' | paste -sd, -)
python3 -c "import sys; s=[int(x) for x in sys.argv[1].split(',')]; sys.exit(0 if s==list(range(1,len(s)+1)) else 1)" "$SEQS" && pass "SSE seq monotonic 1..n ($SEQS)" || fail "seq: $SEQS"

curl -sS -N --max-time 10 "$BASE_URL/v1/tasks/$TASK_ID/events" -H "X-Dev-User-ID: $CONSUMER" -H "Last-Event-ID: 3" > "$TMP/replay.txt"
FIRST=$(grep '^id: ' "$TMP/replay.txt" | head -1 | sed 's/^id: //')
[[ "$FIRST" == 4 ]] && pass "SSE Last-Event-ID replay resumes at seq 4" || fail "replay first id=$FIRST"

code=$(req GET "/v1/tasks/$TASK_ID/artifacts" "$TMP/artifacts.json")
[[ "$code" == 200 ]] || fail "artifacts -> $code"
KINDS=$(json "$TMP/artifacts.json" "','.join(sorted(a['kind'] for a in d['items']))")
[[ "$KINDS" == "LOG,PATCH,SUMMARY,TEST_REPORT" ]] && pass "artifacts: $KINDS" || fail "artifact kinds: $KINDS"
PATCH_ID=$(json "$TMP/artifacts.json" "[a['id'] for a in d['items'] if a['kind']=='PATCH'][0]")
PATCH_SHA=$(json "$TMP/artifacts.json" "[a['sha256'] for a in d['items'] if a['kind']=='PATCH'][0]")
req GET "/v1/tasks/$TASK_ID/artifacts/$PATCH_ID/content" "$TMP/patch.diff" >/dev/null
GOT_SHA=$(python3 -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$TMP/patch.diff")
[[ "$GOT_SHA" == "$PATCH_SHA" ]] && pass "PATCH content matches sha256" || fail "patch sha mismatch"

code=$(req GET /v1/models "$TMP/models.json")
[[ "$code" == 200 && "$(json "$TMP/models.json" "[m['id'] for m in d['data']]")" == "['mock-chat']" ]] && pass "GET /v1/models lists only mock-chat" || fail "models -> $code $(cat "$TMP/models.json")"

code=$(req POST /v1/chat/completions "$TMP/chat.json" -H 'Content-Type: application/json' -d '{"model":"mock-chat","stream":false,"messages":[{"role":"user","content":"Say hello"}]}')
[[ "$code" == 200 && "$(json "$TMP/chat.json" "d['object']")" == chat.completion ]] || fail "chat -> $code $(cat "$TMP/chat.json")"
pass "POST /v1/chat/completions 200 usage=$(json "$TMP/chat.json" "d['usage']")"

code=$(req POST /v1/chat/completions "$TMP/stream.json" -H 'Content-Type: application/json' -d '{"model":"mock-chat","stream":true,"messages":[{"role":"user","content":"hi"}]}')
[[ "$code" == 422 && "$(json "$TMP/stream.json" "d['error']['code']")" == UNSUPPORTED_STREAMING ]] && pass "stream=true -> 422 UNSUPPORTED_STREAMING" || fail "stream -> $code"

code=$(req POST /v1/chat/completions "$TMP/unknown.json" -H 'Content-Type: application/json' -d '{"model":"gpt-personal-plan","messages":[{"role":"user","content":"hi"}]}')
[[ "$code" == 404 && "$(json "$TMP/unknown.json" "d['error']['code']")" == MODEL_NOT_FOUND ]] && pass "unknown alias -> 404 MODEL_NOT_FOUND" || fail "unknown alias -> $code"

echo "SMOKE OK"
