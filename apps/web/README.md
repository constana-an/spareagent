# SpareAgent Friends beta

Minimal Next.js App Router application. Start the configured API, then:

```sh
npm ci --ignore-scripts
FRIENDS_WEB_ORIGIN=http://localhost:3000 SPAREAGENT_API_URL=http://127.0.0.1:8000 npm run dev
```

Set the same `FRIENDS_WEB_ORIGIN` and `FRIENDS_AUTH_ENABLED=true` on the API. No NEXT_PUBLIC
identity, API secret or dev authentication exists in this app. The server relay accepts a
fixed API route set, strips browser identity headers, checks mutation Origin, proxies only
the session cookie, and streams SSE/attachments without buffering. Its HTTP upstream is
limited to loopback; use HTTPS for a separate API host (the compose profile supplies its CA).
The API rejects dev authentication in production. Use HTTPS/private network access for friends.

```sh
npm test
npm run build
npm run typecheck
```

See [operator runbook](../../docs/WEEK5_FRIENDS_BETA_RUNBOOK.md). Invite login, join consent,
device enrollment/connect, sharing/limits, validation statements, task approval/submission,
progress/cancellation, integrity-checked download and participant-scoped audit are supported.
SIMULATED is the default; LIVE must be enabled manually on the API and authorized per task.
Plan remaining is always unavailable. UNVERIFIED results are labelled explicitly.

## Week 6 pilot controls

Onboarding now explains standalone Worker installation and no-inference pilot-check.
History includes owner and requester tasks after completion. Timeline replays persisted
sequence IDs and deduplicates reconnects; result labels distinguish VERIFIED, UNVERIFIED,
FAILED and CANCELLED. Group metrics separate LIVE/SIMULATED and preserve unknowns.
Owner/requester evidence export never certifies physical devices or funding.
See [the current runbook](../../docs/WEEK6_PILOT_RUNBOOK.md) for manual activation.
