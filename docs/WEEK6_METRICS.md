# Pilot observation definitions

`GET /v1/experiments/groups/{group_id}/metrics` returns lifetime aggregates visible only
to active members of that group or its operator. No prompt, result or other owner's
credentials are returned. Dashboard polling is every five seconds; device online means
a non-revoked IDLE/RESERVED/RUNNING node with a heartbeat inside the configured TTL.

- Participating devices: connected records with sharing preferences in this group whose
  owner remains an active member (including paused/revoked connections as participation
  history). Online devices: fresh active nodes among those. These are registered node
  counts, not proof of physically distinct computers.
- Available owner devices: online, idle, connected, VALID reported auth and ENABLED owner
  sharing. This is a snapshot of owner availability, not a dispatch guarantee: operator
  switches, authorization, cooldown, daily limits and task approval still apply.
- LIVE and SIMULATED are always separate. Submitted counts persisted tasks; completed is
  SUCCEEDED execution/result handling; failed is FAILED + TIMED_OUT; cancelled is CANCELLED.
  Completion rate = completed / (completed + failed + cancelled). Nonterminal tasks do
  not enter the denominator. With no terminal tasks, the rate is null, not 100% or 0%.
- Total execution ms: sum of recorded usage-observation durations, including actual failed
  or cancelled execution where reported. It excludes queue time and lacks unreported
  lost/crashed attempts; null means unobserved. Server timestamps are used where available.
- Input/output tokens: sum of available device/runtime observations, not a provider billing
  receipt; null means none observed. Cached/reasoning details are not added twice. Failed
  execution may have consumed usage without reporting token totals.
- Average completed latency ms: mean final task updated_at minus created_at for SUCCEEDED
  tasks, including wait and verification. It excludes failures/cancellations and is null
  until a completed task exists. Small pilots do not support broad performance claims.
- Usage-limit errors: recorded RATE_LIMITED observations associated with this group's tasks;
  same-key completion replay cannot duplicate them.

No money, cost savings, token resale value, exact remaining quota or percentage of plan
allowance is inferred. `plan_remaining` stays `NOT_AVAILABLE`. Read the separate verification
status/report; execution success does not establish semantic correctness. LIVE counters
can also be produced by simulated protocol devices in deterministic tests, so counters
alone do not prove a real pilot. The real evidence file remains NOT_RUN pending the two
physical devices and manual funding confirmation.
