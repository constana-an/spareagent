# Acceptance Matrix and Verification Commands

## P0 runbook
```bash
cp .env.example .env
make up
make migrate
make seed-dev
make lint
make test
make smoke
```
All commands must be defined by generated project. Tests must mock real provider/network activity and use dedicated isolated database.

## Test matrix
| ID | Setup | Action | Expected |
|---|---|---|---|
| AUTH-001 | user A & B | B GETs A's task | 404 or 403, no data leakage |
| AUTH-002 | provider | attempts self-approval | 403 |
| POLICY-001 | personal resource | set public eligible | DB CHECK error |
| POLICY-002 | only personal worker online | create marketplace task | task stays queued; 0 personal inference |
| IDEMP-001 | one user | same key and normalized body twice | same task ID, 1 row |
| IDEMP-002 | one user | same key different body | 409 |
| IDEMP-003 | one user | 10 concurrent same-key POSTs | exactly 1 task row |
| TASK-001 | approved mock worker | create valid task | states ordered; terminal SUCCEEDED |
| TASK-002 | malformed SHA | POST task | 422 |
| TASK-003 | queued task | cancel | CANCELLED, no artifact created afterward |
| LEASE-001 | worker dies | let lease lapse | queued for retry or terminal after max attempts |
| LEASE-002 | completed lease | repeat completion | one recorded completion and one usage record |
| LEASE-003 | revoked lease | complete late | 409 |
| GATE-001 | mock gateway | GET models | only approved aliases |
| GATE-002 | mock gateway | chat request nonstream | response schema correct, usage persisted |
| GATE-003 | gateway | stream=true | 422 UNSUPPORTED_STREAMING |
| GATE-004 | personal-only resource | request gateway alias | not routable |
| BILL-001 | duplicate upstream response ID | record usage twice | one usage row |
| BILL-002 | insufficient task budget | schedule | no attempt begins |
| SEC-001 | sample secret in text | logging | redacted |
| SEC-002 | malicious ../ storage key | access artifact | rejected |
| SEC-003 | APP_ENV=production and DEV_AUTH_ONLY=true | start app | startup fails |

## Mandatory contract quality gates
- `contracts/openapi.yaml` loads with a standards-aware validator.
- JSON outputs serialize exactly documented fields; unknown request fields rejected where indicated.
- All endpoints return consistent `{error:{code,message,request_id}}` for domain errors.
- Schema migration runs on empty PostgreSQL and is immutable once shipped; follow-up amendments are numbered migrations.
- Policy checks validated at DB layer and application layer.
- SSE reconnect using Last-Event-ID never omits already committed events.
- No secrets in source, tracebacks, artifacts or test recordings.
