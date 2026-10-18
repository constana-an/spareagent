# Week 2 implementation status — hybrid remote worker network

Status values: **COMPLETE** (implemented and verified by an executed test), **PARTIAL**
(implemented with a stated gap), **BLOCKED** (not done, with reason).

## Verified run (2026-10-18, Docker Desktop 28.5.1, macOS host), from empty volumes

```
docker compose --profile remote down -v
docker compose up --build -d --wait postgres redis api api-tls && docker compose up -d scheduler worker
make migrate      # applied ['0001_initial', '0003_worker_auth']
make seed-dev     # twice; repeatable
make lint         # ruff check + format --check + OpenAPI 3.1 (23 paths) + event schema: OK
make test         # 226 passed, 0 failed; coverage 90.29 % (gate 80 %)
make smoke        # Week 1 smoke: SMOKE OK
make e2e-remote   # 18/18 checks passed -> reports/week2_e2e_report.json
```
Week 2 integration + e2e tests were additionally run 5× in a row: 25/25 passed each time.
CI (`.github/workflows/ci.yml`) now also runs `make e2e-remote`; it has **not** run on GitHub
for this branch at the time of writing.

## Tasks

| Task | Item | Status | Evidence / notes |
|---|---|---|---|
| 1 | Enrollment: admin or approved-provider single-use tokens; no anonymous registration | COMPLETE | `test_enrollment_token_issuance_rules`, `test_register_rejects_invalid_credentials` |
| 1 | Node credentials separate from consumer credentials | COMPLETE | `test_cross_node_lease_and_path_isolation` (dev auth → 401 on node routes, node token → 401 on consumer routes) |
| 1 | Short-lived node-scoped tokens; expiry, revocation, rotation | COMPLETE | `test_expired_access_token_and_refresh`, `test_expired_node_key`, `test_rotation_revokes_previous_credentials`, `test_admin_revocation_cuts_off_node_and_requeues_work` |
| 1 | No raw secrets at rest or in logs | COMPLETE | `test_secrets_never_stored_raw_or_logged`; demo log scan (0 hits) |
| 1 | Nodes cannot approve their own eligibility | COMPLETE | `extra="forbid"` on registration/heartbeat; `test_heartbeat_requires_valid_node_token` |
| 1 | PERSONAL_SUBSCRIPTION never accepted | COMPLETE | app check + DB trigger + per-claim policy; `test_personal_subscription_node_never_receives_work` |
| 1 | OpenAPI + migration updated | COMPLETE | `contracts/openapi.yaml` v1.1.0; `db/migrations/0003_worker_auth.sql`; contract route-equality test |
| 2 | Installable CLI `spareagent-worker register/start/status/stop` | COMPLETE | `pip install .` exposes the console script (verified in container); demo uses all four commands |
| 2 | Persistent identity, heartbeats, capabilities, lease discovery/accept, mock execution, progress, renewal, cancellation, artifact submission | COMPLETE | `tests/e2e/test_remote_agents.py`, demo |
| 2 | Reconnection with exponential backoff | COMPLETE | `test_worker_survives_api_outage_with_backoff` |
| 2 | Graceful shutdown | COMPLETE | `test_graceful_shutdown_hands_lease_back`; demo `stop` check |
| 2 | Outbound HTTPS; no DB credentials on workers | COMPLETE | demo: workers use `https://api-tls:8443` with dev CA; env scan shows no DB/Redis/pepper vars |
| 3 | Multiple workers, atomic capacity, lease expiry, retry on disconnect | COMPLETE | `test_concurrent_workers_claim_one_task_once`, `test_capacity_exhaustion`, `test_three_workers_disconnect_and_recover`, demo |
| 3 | No duplicate finalization / billing / artifacts | COMPLETE | `test_remote_success_duplicate_completion_and_server_usage`; demo checks |
| 3 | Reconciliation after API or worker restart | COMPLETE | `test_api_restart_during_task_processing`, `test_reconcile_capacity_repairs_drift`, demo worker restart |
| 3 | Stale completion; explicit failure and cancellation states | COMPLETE | `test_stale_completion_after_lease_expiry`, `test_cancellation_during_remote_execution`, `test_server_decides_retryability` |
| 3 | Bounded long polling | COMPLETE | `test_long_poll_returns_when_work_arrives` (server caps at 25 s) |
| 4 | Hybrid execution (local + remote) on one lifecycle/adapter | COMPLETE | Week 1 local-worker tests unchanged and passing; remote tests above |
| 5 | Security test list | COMPLETE | see mapping below |
| 6 | Three-worker demonstration with machine-readable report | COMPLETE | `make e2e-remote` → `reports/week2_e2e_report.json` |

## Task 5 security test mapping

| Requirement | Test |
|---|---|
| Invalid registration credentials | `test_register_rejects_invalid_credentials` |
| Expired node credentials | `test_expired_access_token_and_refresh`, `test_expired_node_key` |
| Credential revocation | `test_admin_revocation_cuts_off_node_and_requeues_work`, `test_rotation_revokes_previous_credentials`, `test_revoked_worker_stops_itself` |
| Cross-node lease access | `test_cross_node_lease_and_path_isolation` |
| Heartbeat from an unauthorized node | `test_heartbeat_requires_valid_node_token`, `test_cross_node_lease_and_path_isolation` |
| Stale completion after lease expiration | `test_stale_completion_after_lease_expiry` |
| Concurrent workers claiming the same task | `test_concurrent_workers_claim_one_task_once` |
| Worker restart during execution | `test_three_workers_disconnect_and_recover` (+ demo SIGKILL/restart) |
| API restart during task processing | `test_api_restart_during_task_processing` |
| Personal subscription routing exclusion | `test_personal_subscription_node_never_receives_work`, `test_enrollment_token_issuance_rules` (+ Week 1 POLICY-001/002, GATE-004) |
| Duplicate completion requests | `test_remote_success_duplicate_completion_and_server_usage` |
| Capacity exhaustion | `test_capacity_exhaustion` |
| Cancellation during execution | `test_cancellation_during_remote_execution` |

## Incomplete items / limitations

1. **Consumer/admin authentication is still dev-only** (`X-Dev-User-ID`, localhost). Minting
   enrollment tokens and admin revocation therefore work only in `APP_ENV=local`. Real
   OIDC/JWT for users remains open (roadmap W4).
2. **TLS is a development CA** generated in compose; there is no mTLS and no certificate
   pinning beyond `--ca-file`. Production must terminate TLS with a real certificate.
3. **Long poll uses DB polling** (every 0.5 s per waiting worker). Fine for the 3-worker lab,
   not tuned for large fleets.
4. **Prompt redaction is pattern-based**; remote nodes still see task text. Private
   repositories/prompts on community nodes remain out of scope (PRD).
5. **No node reputation.** A malicious node can fail its leases (burning up to
   `max_attempts`) or return low-quality but structurally valid mock artifacts. Verification
   is still the W1 structural verifier; independent verification is W3.
6. **Enrollment tokens are passed to demo containers via environment variables** (visible to
   `docker inspect`). They are single use and short-lived; production should deliver them
   out of band.
7. `rotate` is automatic (when the node key is within 7 days of expiry) rather than a CLI
   subcommand; the API endpoint exists.
8. The project still lives in an iCloud-synced folder (see Week 1 note).
