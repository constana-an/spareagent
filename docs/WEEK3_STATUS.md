# Week 3 implementation status — secure code execution & independent verification

Status values: **COMPLETE** (implemented and verified by an executed test), **PARTIAL**
(implemented with a stated gap), **BLOCKED** (not done, with reason).

> **Code execution remains disabled by default** (`CODE_EXECUTION_ENABLED=false`) and is
> limited to an allowlisted fixture catalog and explicitly allowlisted resources. Passing tests
> do **not** make it safe for arbitrary repositories, real customer workloads, or untrusted
> community nodes. See residual risks below.

## Verified run (2026-10-18, Docker Desktop 28.5.1 / arm64 macOS host), from empty volumes

```
make clean && make up && make migrate   # applied 0001, 0003, 0005
make seed-dev && make seed-dev          # repeatable
make lint                               # ruff + format + OpenAPI 3.1 (23 paths): OK
SPAREAGENT_NETWORK_TESTS=1 make test    # 319 passed, 0 failed; coverage 88.98 % (gate 80 %)
make smoke                              # Week 1: SMOKE OK
make e2e-remote                         # Week 2: 18/18 -> reports/week2_e2e_report.json
make e2e-code                           # Week 3: all checks passed -> reports/week3_e2e_report.json
```
Week 3 sandbox/verification/e2e test files were also run 3× consecutively: 42 passed each time
(2 real-GitHub tests skipped without the network flag). GitHub Actions has **not** run this
branch yet at the time of writing; CI now builds the sandbox image, mounts the Docker socket
into the test container and enables the network tests.

## Phases

| Phase | Item | Status | Evidence |
|---|---|---|---|
| 1 | HTTPS-GitHub-only URLs, owner/name validation, SSRF/redirect refusal | COMPLETE | `test_malicious_repository_urls_rejected` (14 cases), `http.followRedirects=false` |
| 1 | Exact pinned commit, HEAD verified | COMPLETE | `test_pinned_commit_checkout`, `test_real_github_pinned_checkout` |
| 1 | Invalid/unavailable commit; auth-required repos rejected | COMPLETE | `test_rejected_repositories`, `test_real_github_unavailable_commit_and_private_repo` |
| 1 | No submodules / LFS / symlinks; size + timeout limits | COMPLETE | `test_rejected_repositories`, `test_oversized_repository` |
| 1 | No hooks, credential helpers or host git config; no consumer credentials | COMPLETE | `test_host_git_config_and_credentials_ignored` |
| 1 | Isolated temp checkout, removed on cleanup | COMPLETE | `test_pinned_commit_checkout` |
| 2 | Hardened sandbox (no network, read-only, non-root, caps dropped, NNP, limits, tmpfs quota) | COMPLETE | `test_sandbox_health`, `test_sandbox_filesystem_limits`, `test_sandbox_memory_and_process_limits` |
| 2 | No Docker socket / host paths / host env in sandbox | COMPLETE | `test_sandbox_cannot_see_host_secrets`, isolation-probe fixture, `test_unsafe_docker_arguments_refused` |
| 2 | Network disabled during execution | COMPLETE | `test_sandbox_network_disabled` (only loopback interface; 5 targets blocked) |
| 2 | Timeout / cancellation with forced cleanup; stale reaping | COMPLETE | `test_sandbox_timeout_and_cleanup`, `test_sandbox_cancellation_and_cleanup`, `test_reaper_removes_stale_sandboxes` |
| 2 | Rootless / user-namespace isolation | **PARTIAL** | Health check detects and reports it; `require_userns` option exists. The dev daemon (Docker Desktop) is neither rootless nor userns-remapped, so it is **not enforced** here. |
| 3 | Deterministic CodeExecutionAdapter: lease → checkout → allowlisted edit → sandboxed trusted tests → patch → artifacts → cleanup | COMPLETE | `test_code_adapter_fixes_fixture` (Python + TypeScript), `test_full_code_fix_flow` |
| 3 | Extensible for a model-backed AgentAdapter | COMPLETE | `EditStrategy` protocol (D-42) |
| 4 | Independent platform verifier (base SHA, schema, integrity, patch policy, clean apply, fresh sandbox, expected vs actual, worker-claim comparison, separate records) | COMPLETE | `test_honest_submission_verified_and_published`, `test_invalid_submissions_fail_verification` (8 cases), `test_forged_success_fails_verification_then_retry_succeeds` |
| 4 | Worker self-reported success never sufficient | COMPLETE | VERIFYING gate + DB triggers; `test_database_refuses_unverified_code_success` |
| 5 | Artifacts: PATCH, TEST_REPORT, EXECUTION_LOG, SUMMARY, VERIFICATION_REPORT; SHA-256; size/type/name limits; no duplicates; ownership; post-expiry rejection | COMPLETE | `test_duplicate_completion_verifies_once`, `test_artifacts_after_lease_expiry_rejected`, Week 1 ownership tests |
| 6 | Security & integration test list | COMPLETE | mapping below |
| 7 | End-to-end demo incl. invalid-patch and host-secret demonstrations | COMPLETE | `make e2e-code` → `reports/week3_e2e_report.json` |

## Phase 6 test mapping

| Requirement | Test(s) |
|---|---|
| Correct pinned commit checkout | `test_pinned_commit_checkout`, `test_real_github_pinned_checkout` |
| Invalid or unavailable commit | `test_rejected_repositories[python-calc-0000…]`, `test_real_github_unavailable_commit_and_private_repo` |
| Oversized repository | `test_oversized_repository` |
| Malicious repository URL | `test_malicious_repository_urls_rejected` |
| Path traversal in generated patch | `test_rejected_patches`, `test_invalid_submissions_fail_verification[…PATCH_POLICY]`, demo forged patch |
| Symlink escape attempts | `test_rejected_repositories[evil-symlink]`, `test_workspace_tar_rejects_symlinks_and_excludes_git`, symlink-mode patch case |
| Sandbox access to host secrets | `test_sandbox_cannot_see_host_secrets`, `test_isolation_probe_fixture_passes_only_when_isolated`, demo probe |
| Blocked network access during execution | `test_sandbox_network_disabled`, probe `test_network_blocked` |
| Container timeout and cleanup | `test_sandbox_timeout_and_cleanup`, `test_code_adapter_timeout_kills_sandbox` |
| Cancellation during execution | `test_sandbox_cancellation_and_cleanup`, `test_cancellation_during_verification` |
| Worker disconnection during execution | `test_worker_disconnect_during_sandbox_run_recovers` |
| Invalid test report | `test_invalid_reports`, `test_invalid_submissions_fail_verification[…INVALID_TEST_REPORT]` |
| Forged success result | `test_forged_success_fails_verification_then_retry_succeeds`, `…[WORKER_REPORT_MISMATCH]`, demo |
| Duplicate completion | `test_duplicate_completion_verifies_once` |
| Verification failure and retry behaviour | `test_forged_success_…`, `test_verifier_infrastructure_errors_retry_then_fail` |
| Personal subscription resource exclusion | `test_personal_subscription_resource_never_runs_code` (+ Week 1/2 policy tests) |

## Defects found and fixed during this week
1. **Stale-process bypass** — a long-running pre-Week-3 local worker claimed CODE tasks and
   completed them with mock output, skipping verification. Fixed with DB triggers (D-39);
   the demo now force-recreates platform processes.
2. **Artifact merge** — two artifact kinds with identical bytes collapsed into one
   content-addressed row. Such submissions are now refused (D-40).
3. **`/home` visible in the sandbox** (from the node base image) — removed from the image;
   the isolation probe enforces it.
4. **Week 2 race, exposed by GitHub Actions timing** — a node's final `OFFLINE` heartbeat
   during graceful shutdown could be undone by its own still-running long poll on the server,
   which re-marked the node ONLINE each iteration. Only the first poll iteration may now
   re-online a node; later iterations refresh liveness only and stop claiming once the node is
   OFFLINE/DRAINING (`test_offline_announcement_not_undone_by_inflight_poll`, verified to fail
   on the old code).

## Residual security risks (explicit)
1. **Shared kernel.** Sandboxes are ordinary Docker containers. A kernel or runtime escape
   compromises the node host. Required before untrusted workloads: gVisor/Kata/Firecracker (or
   at least a rootless / userns-remapped daemon, enforced via `require_userns`), custom
   seccomp/AppArmor profiles, and a separate review.
2. **Docker socket = host root.** The verifier and code-worker drive the daemon via the
   mounted socket. A compromise of those processes (e.g., via a git or tar parsing bug on
   attacker data) is a host compromise. They run only trusted code, but parse untrusted input.
3. **Host-side git on untrusted data.** Fetch, tree listing, checkout, diff and `git apply`
   run outside the sandbox (hardened config, fsck, pre-vetted patches). Git vulnerabilities
   remain an exposure; moving these steps into a sandbox is a follow-up.
4. **Verification strength.** The verifier re-runs the same trusted tests the recipe defines;
   it proves "these tests pass on this patch", not general correctness. Model-generated patches
   will need hidden/held-out tests and review.
5. **Self-reported capability.** `sandbox=docker` is a matching hint; a lying node only
   wastes an attempt (bounded by `max_attempts`, budget) because verification is independent.
6. **Supply chain.** The sandbox base image (`node:22-alpine`) and the Docker CLI download are
   tag/version-pinned, not digest-pinned or signature-verified.
7. **Verifier throughput/DoS.** One verification at a time per verifier; floods of bad
   submissions are bounded only by attempts/budget and rate limits are not yet node-specific.
8. **Fixture transport is dev-only.** `REPO_FIXTURE_ROOT` is refused outside `APP_ENV=local`;
   production must not configure it.
9. Week 2 items still open: dev-only user auth, dev CA TLS, pattern-based redaction.
10. **Environment:** the working copy is in iCloud Drive; iCloud evicted 72 project files
    mid-session (reads hang until re-downloaded). Keep it downloaded or move it out of iCloud.
