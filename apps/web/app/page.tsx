"use client";
import { FormEvent, useCallback, useEffect, useState } from "react";

type Me = {
  user_id: string;
  role: string;
  terms_version: string;
  live_enabled: boolean;
};
type Group = {
  id: string;
  name: string;
  status: string;
  sharing_enabled: boolean;
  max_members: number;
  members: { user_id: string; status: string }[];
  authorization: null | {
    authorization_reference: string;
    expires_at: string;
    live_execution_enabled: boolean;
  };
};
type Prefs = {
  group_id: string;
  sharing_state: string;
  max_tasks_per_day: number;
  max_task_duration_seconds: number;
};
type Device = {
  connection: {
    id: string;
    worker_id: string;
    device_label: string;
    auth_state: string;
    owner_check_passed_at: string | null;
    no_purchased_credits_at: string | null;
  };
  worker_status: string;
  last_heartbeat: string | null;
  preferences: Prefs | null;
};
type Node = {
  connection_id: string;
  owner_id: string;
  device_label: string;
  online: boolean;
  requires_approval: boolean;
  max_task_duration_seconds: number;
};
type Task = {
  id: string;
  group_id: string;
  status: string;
  task_type: string;
  execution_mode: string;
  prompt: string;
  repository_url: string;
  commit_sha: string;
  max_duration_seconds: number;
  approval_status: string;
  owner_id: string;
  verification_status: string;
  result_summary: string | null;
  last_decision: null | { reasons: string[] };
};
type Usage = {
  connection_id: string;
  observed_input_tokens: number;
  observed_output_tokens: number;
  tasks_started_last_24h: number;
  capacity_state: string;
  last_observation_at: string | null;
  reset_hint: string | null;
  local_limits: null | { max_tasks_per_day: number };
};
type Audit = {
  id: number;
  task_id: string;
  decision: string;
  reasons: string[];
  execution_mode: string;
  created_at: string;
};
type Metrics = {
  participating_devices: number;
  online_devices: number;
  available_owner_devices: number;
  modes: Record<
    string,
    {
      submitted: number;
      completed: number;
      failed: number;
      cancelled: number;
      success_rate: number | null;
      success_rate_denominator: number;
      total_execution_ms: number | null;
      observed_input_tokens: number | null;
      observed_output_tokens: number | null;
      average_completed_latency_ms: number | null;
      usage_limit_errors: number;
      observations: number;
    }
  >;
};
type TimelineEvent = {
  seq: number;
  type: string;
  created_at?: string;
  payload: { status?: string; message?: string };
};
function resultLabel(t: Task): string {
  if (t.status === "CANCELLED" || t.status === "CANCELLING") return t.status;
  if (
    ["FAILED", "TIMED_OUT"].includes(t.status) ||
    ["FAILED", "ERROR"].includes(t.verification_status)
  )
    return "FAILED";
  if (t.status === "SUCCEEDED")
    return t.verification_status === "PASSED" ? "VERIFIED" : "UNVERIFIED";
  return t.status;
}
const eventLabels: Record<string, string> = {
  "task.created": "Request recorded",
  "task.queued": "Waiting for approval or capacity",
  "task.reserved": "Device reserved",
  "task.started": "Execution started",
  "task.progress": "Execution update",
  "task.verifying": "Verification stage",
  "task.succeeded": "Execution and result handling completed",
  "task.failed": "Failed",
  "task.cancelled": "Cancelled",
  "task.timed_out": "Timed out",
  "task.retrying": "Retry pending",
};
type Artifact = { id: string; kind: string; source: string; sha256: string };
const terminal = new Set(["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"]);
const joinAcks = [
  "non_commercial_experimental_use",
  "no_payment_or_compensation",
  "participation_revocable_any_time",
];
const sharingAcks = [
  ...joinAcks,
  "subscription_usage_may_be_consumed_within_confirmed_scope",
  "device_executes_opted_in_workloads",
  "owner_controls_local_credentials",
  "owner_may_add_restrictions",
];
const labels: Record<string, string> = {
  non_commercial_experimental_use:
    "This is a private, non-commercial experiment.",
  no_payment_or_compensation: "There is no payment, resale or compensation.",
  participation_revocable_any_time:
    "I can pause or revoke my participation at any time.",
  subscription_usage_may_be_consumed_within_confirmed_scope:
    "Approved tasks consume my subscription usage within the recorded scope.",
  device_executes_opted_in_workloads: "My device executes the tasks I approve.",
  owner_controls_local_credentials:
    "My ChatGPT credentials stay on my own device.",
  owner_may_add_restrictions:
    "I control task approval and can add restrictions.",
};
async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
  key?: string,
): Promise<T> {
  const r = await fetch("/api/" + path, {
    method,
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(key ? { "Idempotency-Key": key } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    const e = await r.json().catch(() => null);
    throw new Error(e?.error?.message || `Request failed (${r.status})`);
  }
  return r.status === 204 ? (undefined as T) : r.json();
}
function Checks({
  names,
  values,
  set,
}: {
  names: string[];
  values: Record<string, boolean>;
  set: (x: Record<string, boolean>) => void;
}) {
  return (
    <div className="checks">
      {names.map((k) => (
        <label key={k}>
          <input
            type="checkbox"
            checked={!!values[k]}
            onChange={(e) => set({ ...values, [k]: e.target.checked })}
          />
          {labels[k]}
        </label>
      ))}
    </div>
  );
}
export default function Dashboard() {
  const [me, setMe] = useState<Me | null>(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  const [groups, setGroups] = useState<Group[]>([]),
    [gid, setGid] = useState(""),
    [devices, setDevices] = useState<Device[]>([]),
    [nodes, setNodes] = useState<Node[]>([]),
    [usage, setUsage] = useState<Usage[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]),
    [incoming, setIncoming] = useState<Task[]>([]),
    [audit, setAudit] = useState<Audit[]>([]),
    [selected, setSelected] = useState<Task | null>(null),
    [events, setEvents] = useState<TimelineEvent[]>([]),
    [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [joinChecks, setJoinChecks] = useState<Record<string, boolean>>({}),
    [shareChecks, setShareChecks] = useState<Record<string, boolean>>({});
  const [enrollment, setEnrollment] = useState<{
    resource_id: string;
    token: string;
    expires_at: string;
  } | null>(null);
  const group = groups.find((g) => g.id === gid),
    member = group?.members.find((m) => m.user_id === me?.user_id),
    active = member?.status === "ACTIVE";
  const refresh = useCallback(async () => {
    if (!me) return;
    const [gs, ds, us, ts, ins] = await Promise.all([
      api<Group[]>("experiments/groups"),
      api<{ items: Device[] }>("subscriptions/status"),
      api<{ items: Usage[] }>("subscriptions/usage"),
      api<{ items: Task[] }>("experimental-tasks"),
      api<{ items: Task[] }>("experimental-tasks?role=owner"),
    ]);
    setGroups(gs);
    setDevices(ds.items);
    setUsage(us.items);
    setTasks(ts.items);
    setIncoming(ins.items);
    if (!gid && gs[0]) setGid(gs[0].id);
    const current = gs.find((g) => g.id === gid);
    if (
      current?.members.some(
        (m) => m.user_id === me.user_id && m.status === "ACTIVE",
      )
    ) {
      const [ns, log, ms] = await Promise.all([
        api<{ items: Node[] }>(`experimental-tasks/nodes?group_id=${gid}`),
        api<Audit[]>(`friends/groups/${gid}/audit`),
        api<Metrics>(`experiments/groups/${gid}/metrics`),
      ]);
      setNodes(ns.items);
      setAudit(log);
      setMetrics(ms);
    } else {
      setNodes([]);
      setAudit([]);
      setMetrics(null);
    }
  }, [me, gid]);
  useEffect(() => {
    api<Me>("friends/me")
      .then(setMe)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => {
    if (!me) return;
    const run = () => refresh().catch((e) => setError(e.message));
    run();
    const id = setInterval(run, 5000);
    return () => clearInterval(id);
  }, [me, refresh]);
  async function act(
    fn: () => Promise<unknown>,
    message = "Saved",
    reload = true,
  ) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
      setNotice(message);
      if (reload) await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    if (!selected) return;
    let closed = false;
    let finished = false;
    setEvents([]);
    setArtifacts([]);
    const stream = new EventSource(`/api/tasks/${selected.id}/events`);
    const update = async () => {
      try {
        const t = await api<Task>(`experimental-tasks/${selected.id}`);
        if (closed) return;
        setSelected(t);
        const a = await api<{ items: Artifact[] }>(
          `tasks/${selected.id}/artifacts`,
        );
        if (!closed) setArtifacts(a.items);
      } catch (e) {
        if (!closed) setError((e as Error).message);
      }
    };
    const listen = (raw: MessageEvent) => {
      const e = JSON.parse(raw.data);
      setEvents((old) =>
        [...old.filter((x) => x.seq !== e.seq), e]
          .sort((a, b) => a.seq - b.seq)
          .slice(-100),
      );
      if (
        [
          "task.succeeded",
          "task.failed",
          "task.cancelled",
          "task.timed_out",
        ].includes(e.type)
      ) {
        finished = true;
        stream.close();
      }
      void update();
    };
    for (const name of [
      "task.created",
      "task.reserved",
      "task.started",
      "task.progress",
      "task.verifying",
      "task.succeeded",
      "task.failed",
      "task.cancelled",
      "task.timed_out",
      "task.cancelling",
      "task.queued",
      "task.retrying",
    ])
      stream.addEventListener(name, listen as EventListener);
    stream.onerror = () => {
      if (!closed && !finished)
        setNotice(
          "Event stream reconnecting; task state is refreshed automatically.",
        );
    };
    void update();
    const timer = setInterval(update, 5000);
    return () => {
      closed = true;
      stream.close();
      clearInterval(timer);
    };
  }, [selected?.id]);
  async function download(a: Artifact) {
    const r = await fetch(
      `/api/tasks/${selected!.id}/artifacts/${a.id}/content`,
      { cache: "no-store" },
    );
    if (!r.ok) throw new Error("Artifact unavailable");
    const bytes = await r.arrayBuffer(),
      hash = Array.from(
        new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
      )
        .map((x) => x.toString(16).padStart(2, "0"))
        .join("");
    if (hash !== a.sha256 || r.headers.get("x-artifact-sha256") !== a.sha256)
      throw new Error("Artifact integrity check failed");
    const url = URL.createObjectURL(new Blob([bytes]));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${a.kind.toLowerCase()}-${a.id}${a.kind === "VERIFICATION_REPORT" ? ".json" : ""}`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const taskHistory = Array.from(
    new Map([...tasks, ...incoming].map((t) => [t.id, t])).values(),
  );
  if (loading)
    return (
      <main className="login">
        <p>Opening your private workspace…</p>
      </main>
    );
  if (!me)
    return (
      <main className="login">
        <div className="mark">
          S<span>•</span>
        </div>
        <p className="eyebrow">SPAREAGENT / FRIENDS BETA</p>
        <h1>
          A little spare capacity.
          <br />A trusted circle.
        </h1>
        <p>Sign in with the one-time code your operator shared privately.</p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            void act(async () => {
              setMe(
                await api<Me>("friends/login", "POST", {
                  token: f.get("token"),
                }),
              );
            }, "Signed in");
          }}
        >
          <label>
            Invitation code
            <input
              name="token"
              type="password"
              autoComplete="off"
              required
              minLength={20}
              maxLength={256}
            />
          </label>
          <button disabled={busy}>
            Enter workspace <span>↗</span>
          </button>
        </form>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <small>
          Invite only · No payments · Credentials stay on your device
        </small>
      </main>
    );
  return (
    <div className="shell">
      <aside>
        <div className="brand">
          <div className="mark">
            S<span>•</span>
          </div>
          SpareAgent
        </div>
        <p className="eyebrow">FRIENDS BETA / 06</p>
        <nav>
          <a href="#overview">Overview</a>
          <a href="#devices">Your devices</a>
          <a href="#requests">
            Requests{" "}
            <b>
              {
                incoming.filter(
                  (t) =>
                    t.approval_status === "PENDING" && !terminal.has(t.status),
                ).length
              }
            </b>
          </a>
          <a href="#submit">New review</a>
          <a href="#history">Audit history</a>
        </nav>
        <div className="aside-bottom">
          <span className="dot" /> Private experiment
          <p>
            Voluntary. Revocable.
            <br />
            Always owner controlled.
          </p>
          <button
            className="quiet"
            onClick={() =>
              void act(
                async () => {
                  await api("friends/logout", "POST");
                  setMe(null);
                  setSelected(null);
                  setEnrollment(null);
                },
                "Signed out",
                false,
              )
            }
          >
            Sign out
          </button>
        </div>
      </aside>
      <main>
        <header>
          <div>
            <p className="eyebrow">YOUR PRIVATE WORKSPACE</p>
            <h1>Good work, shared carefully.</h1>
            <p>Small tasks. Explicit permission. Your device, your call.</p>
          </div>
          <span className="pill">
            {me.live_enabled ? "LIVE gate enabled" : "LIVE disabled"}
          </span>
        </header>
        <div role="status" aria-live="polite">
          {notice && <p className="notice">{notice}</p>}
        </div>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <section id="overview" className="overview">
          <div>
            <label>
              Experimental group
              <select
                value={gid}
                onChange={(e) => {
                  setGid(e.target.value);
                  setShareChecks({});
                  setJoinChecks({});
                }}
              >
                {groups.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name}
                  </option>
                ))}
              </select>
            </label>
            {!groups.length && (
              <p>No group invitations. Ask your operator for access.</p>
            )}
            <span className="muted">
              {group?.members.filter((m) => m.status === "ACTIVE").length || 0}{" "}
              active participants · maximum {group?.max_members || 5} in this
              group
            </span>
          </div>
          <div>
            <p className="eyebrow">AUTHORIZATION</p>
            <strong>
              {group?.authorization?.authorization_reference || "Not recorded"}
            </strong>
            <p className="muted">
              {group?.authorization
                ? `Expires ${new Date(group.authorization.expires_at).toLocaleDateString()}`
                : "Operator review required"}
            </p>
          </div>
        </section>
        {group && !active && (
          <section>
            <h2>Accept your group invitation</h2>
            <Checks names={joinAcks} values={joinChecks} set={setJoinChecks} />
            <button
              disabled={busy || !joinAcks.every((k) => joinChecks[k])}
              onClick={() =>
                void act(
                  () =>
                    api(`experiments/groups/${gid}/join`, "POST", {
                      terms_version: me.terms_version,
                      acknowledgements: joinChecks,
                    }),
                  "Joined group",
                )
              }
            >
              Join {group.name}
            </button>
          </section>
        )}
        <div className="stats">
          <article>
            <span>Connected devices</span>
            <strong>
              {devices
                .filter(
                  (d) =>
                    d.worker_status !== "OFFLINE" &&
                    d.last_heartbeat &&
                    Date.now() - Date.parse(d.last_heartbeat) < 30000,
                )
                .length.toString()
                .padStart(2, "0")}
            </strong>
            <small>Recently observed online</small>
          </article>
          <article>
            <span>Tasks started / 24h</span>
            <strong>
              {usage
                .reduce((n, u) => n + u.tasks_started_last_24h, 0)
                .toString()
                .padStart(2, "0")}
            </strong>
            <small>Platform observations</small>
          </article>
          <article>
            <span>Plan remaining</span>
            <strong className="unavailable">Not available</strong>
            <small>Check your ChatGPT usage page</small>
          </article>
        </div>
        <section id="devices">
          <div className="section-head">
            <div>
              <p className="eyebrow">OWNER CONTROLS</p>
              <h2>Your connected devices</h2>
            </div>
            <span className="muted">One task at a time</span>
          </div>
          {!devices.length && (
            <p className="empty">
              No device connected yet. Register your own Mac to get started.
            </p>
          )}
          {devices.map((d) => {
            const u = usage.find((x) => x.connection_id === d.connection.id),
              fresh =
                !!d.last_heartbeat &&
                Date.now() - Date.parse(d.last_heartbeat) < 30000;
            return (
              <article className="device" key={d.connection.id}>
                <div>
                  <h3>
                    <span
                      className={
                        "dot " +
                        (fresh && d.worker_status !== "OFFLINE" ? "" : "off")
                      }
                    />
                    {d.connection.device_label}
                  </h3>
                  <p>
                    {fresh ? d.worker_status : "OFFLINE / stale heartbeat"} ·
                    ChatGPT {d.connection.auth_state} ·{" "}
                    {d.preferences?.sharing_state || "Not sharing"}
                  </p>
                  <small>
                    {d.last_heartbeat
                      ? `Last seen ${new Date(d.last_heartbeat).toLocaleString()}`
                      : "Heartbeat not observed"}
                  </small>
                </div>
                <div className="actions">
                  <button
                    className="quiet"
                    disabled={busy || !d.preferences}
                    onClick={() =>
                      void act(
                        () =>
                          api("sharing/pause", "POST", {
                            connection_id: d.connection.id,
                          }),
                        "Sharing paused; queued tasks wait",
                      )
                    }
                  >
                    Pause
                  </button>
                  <button
                    className="danger"
                    disabled={busy || !d.preferences}
                    onClick={() =>
                      void act(
                        () =>
                          api("sharing/revoke", "POST", {
                            connection_id: d.connection.id,
                          }),
                        "Sharing revoked; unfinished tasks are cancelling",
                      )
                    }
                  >
                    Revoke
                  </button>
                </div>
                <div className="device-detail">
                  <span>
                    {u?.tasks_started_last_24h || 0} /{" "}
                    {d.preferences?.max_tasks_per_day ?? "—"} tasks in rolling
                    24h
                  </span>
                  <span>
                    {u?.last_observation_at ? u.observed_input_tokens : "Unknown"} input /{" "}
                    {u?.last_observation_at ? u.observed_output_tokens : "Unknown"} output tokens observed
                  </span>
                  <span>Capacity: {u?.capacity_state || "UNKNOWN"}</span>
                  {u?.reset_hint && <span>{u.reset_hint}</span>}
                </div>
                <details>
                  <summary>Configure limits, attest and enable sharing</summary>
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      const f = new FormData(e.currentTarget);
                      void act(async () => {
                        await api("sharing/preferences", "PUT", {
                          connection_id: d.connection.id,
                          group_id: gid,
                          max_concurrent_tasks: 1,
                          max_tasks_per_day: Number(f.get("daily")),
                          max_task_duration_seconds: Number(f.get("duration")),
                          require_owner_approval: true,
                          advance_consent_requester_ids: [],
                          allowed_task_types: ["CODE_REVIEW"],
                          allow_paid_credits: false,
                        });
                        await api("sharing/enable", "POST", {
                          connection_id: d.connection.id,
                          terms_version: me.terms_version,
                          acknowledgements: shareChecks,
                        });
                      }, "Sharing enabled with per-task approval");
                    }}
                  >
                    <div className="form-row">
                      <label>
                        Daily task limit
                        <input
                          name="daily"
                          type="number"
                          min="1"
                          max="50"
                          defaultValue={d.preferences?.max_tasks_per_day ?? 3}
                          required
                        />
                      </label>
                      <label>
                        Hard timeout (seconds)
                        <input
                          name="duration"
                          type="number"
                          min="10"
                          max="60"
                          defaultValue={
                            d.preferences?.max_task_duration_seconds ?? 60
                          }
                          required
                        />
                      </label>
                    </div>
                    <Checks
                      names={sharingAcks}
                      values={shareChecks}
                      set={setShareChecks}
                    />
                    <button
                      disabled={
                        busy ||
                        !active ||
                        !sharingAcks.every((k) => shareChecks[k])
                      }
                    >
                      Enable / resume sharing
                    </button>
                  </form>
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      const f = new FormData(e.currentTarget);
                      void act(
                        () =>
                          api("subscriptions/attest", "POST", {
                            connection_id: d.connection.id,
                            codex_check_result: f.get("check"),
                            no_purchased_credits: f.get("credits") === "on",
                            no_auto_reload: f.get("reload") === "on",
                          }),
                        "Attestation recorded",
                      );
                    }}
                  >
                    <h3>Local validation statements</h3>
                    <label>
                      Actual codex-check result
                      <select
                        name="check"
                        defaultValue="AUTH_OK_INFERENCE_SKIPPED"
                      >
                        <option>AUTH_OK_INFERENCE_SKIPPED</option>
                        <option>OWNER_ONLY_SUBSCRIPTION_RUN_OK</option>
                        <option>FAILED</option>
                      </select>
                    </label>
                    <label className="check">
                      <input name="credits" type="checkbox" />
                      No additional purchased or granted credits
                    </label>
                    <label className="check">
                      <input name="reload" type="checkbox" />
                      Automatic credit reload is off
                    </label>
                    <button className="quiet" disabled={busy}>
                      Record / withdraw attestations
                    </button>
                    <small>
                      Login and credit balance are owner statements. Credits are
                      not observable by SpareAgent.
                    </small>
                  </form>
                </details>
              </article>
            );
          })}
          <details>
            <summary>Connect your own Mac</summary>
            <p>
              Friends install only the official Codex CLI and the standalone
              Worker. The operator runs the coordinator. You do not need Docker,
              PostgreSQL, Redis or backend credentials.
            </p>
            <ol className="setup-steps">
              <li>
                Get the reviewed macOS setup script, Worker wheel and SHA-256
                from your operator.
                <code>
                  bash setup-worker-macos.sh
                  ./spareagent_worker-0.6.0-py3-none-any.whl SHA256
                </code>
              </li>
              <li>
                Run <code>codex login</code> locally with your own ChatGPT Plus
                account.
              </li>
              <li>
                Create your enrollment token below and register this Mac. This
                is a SpareAgent token; never paste ChatGPT credentials.
              </li>
              <li>
                Connect the returned worker ID, set CODE_REVIEW / 60 seconds /
                one task, and accept sharing terms.
              </li>
              <li>
                Run <code>spareagent-worker pilot-check</code>. It uses no
                inference and lists blocked conditions separately from
                owner/operator attestations.
              </li>
            </ol>
            <p className="notice">
              LIVE starts off. Real validation consumes subscription usage only
              when you manually opt in. Confirm no extra purchased or granted
              Credits and no auto-reload before every real task.
            </p>
            <p>
              <a
                href="https://github.com/constana-an/spareagent/blob/main/docs/WEEK6_PILOT_RUNBOOK.md"
                target="_blank"
                rel="noreferrer"
              >
                Controlled two-device runbook ↗
              </a>
            </p>
            <button
              className="quiet"
              disabled={busy || !active}
              onClick={() =>
                void act(
                  async () =>
                    setEnrollment(
                      await api("subscriptions/enrollment-tokens", "POST", {}),
                    ),
                  "Private enrollment token created",
                )
              }
            >
              Create one-time enrollment token
            </button>
            {enrollment && (
              <div className="notice">
                <p>
                  Resource: <code>{enrollment.resource_id}</code>
                </p>
                <label>
                  Private enrollment token (expires{" "}
                  {new Date(enrollment.expires_at).toLocaleTimeString()})
                  <textarea readOnly value={enrollment.token} />
                </label>
                <p>
                  Use this token locally with{" "}
                  <code>spareagent-worker register</code>. Do not share it or
                  paste ChatGPT credentials here.
                </p>
                <button className="quiet" onClick={() => setEnrollment(null)}>
                  Hide token
                </button>
              </div>
            )}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                const f = new FormData(e.currentTarget);
                void act(
                  () =>
                    api("subscriptions/connect", "POST", {
                      worker_id: f.get("worker"),
                      device_label: f.get("label"),
                      subscription_type: "PLUS",
                      auth_method: "CHATGPT",
                    }),
                  "Device connected",
                );
              }}
            >
              <label>
                Registered worker ID
                <input
                  name="worker"
                  required
                  pattern="[0-9a-fA-F-]{36}"
                  placeholder="From spareagent-worker register"
                />
              </label>
              <label>
                Device name
                <input
                  name="label"
                  maxLength={80}
                  required
                  placeholder="Alice’s Mac"
                />
              </label>
              <label className="check">
                <input type="checkbox" required />I verified a local ChatGPT
                login on this device.
              </label>
              <button disabled={busy || !active}>Connect device</button>
            </form>
          </details>
        </section>
        <div className="columns">
          <section id="requests">
            <p className="eyebrow">YOUR PERMISSION FIRST</p>
            <h2>Incoming requests</h2>
            {!incoming.filter((t) => t.group_id === gid).length && (
              <p className="empty">
                Your circle is quiet. New requests appear here.
              </p>
            )}
            {incoming
              .filter((t) => t.group_id === gid)
              .map((t) => (
                <article className="request" key={t.id}>
                  <div className="section-head">
                    <h3>{t.task_type.replaceAll("_", " ")}</h3>
                    <span className="pill">{t.execution_mode}</span>
                  </div>
                  <p>{t.prompt}</p>
                  <p className="muted break">
                    {t.repository_url}
                    <br />
                    <code>{t.commit_sha}</code>
                    <br />
                    Hard timeout: {t.max_duration_seconds}s
                  </p>
                  <div className="actions">
                    {t.approval_status === "PENDING" &&
                      !terminal.has(t.status) && (
                        <>
                          <button
                            disabled={busy}
                            onClick={() =>
                              void act(
                                () =>
                                  api(
                                    `experimental-tasks/${t.id}/approve`,
                                    "POST",
                                  ),
                                "Task approved",
                              )
                            }
                          >
                            Approve task
                          </button>
                          <button
                            className="quiet"
                            disabled={busy}
                            onClick={() =>
                              void act(
                                () =>
                                  api(
                                    `experimental-tasks/${t.id}/reject`,
                                    "POST",
                                  ),
                                "Task rejected",
                              )
                            }
                          >
                            Reject
                          </button>
                        </>
                      )}
                    <button className="quiet" onClick={() => setSelected(t)}>
                      Inspect {t.approval_status.toLowerCase()}
                    </button>
                  </div>
                </article>
              ))}
          </section>
          <section id="submit">
            <p className="eyebrow">ASK YOUR CIRCLE</p>
            <h2>Submit a code review</h2>
            <form
              onSubmit={(e: FormEvent<HTMLFormElement>) => {
                e.preventDefault();
                const f = new FormData(e.currentTarget);
                void act(async () => {
                  const t = await api<Task>(
                    "experimental-tasks",
                    "POST",
                    {
                      task_type: "CODE_REVIEW",
                      group_id: gid,
                      connection_id: f.get("connection"),
                      repository_url: f.get("repo"),
                      commit_sha: f.get("sha"),
                      prompt: f.get("prompt"),
                      max_duration_seconds: Number(f.get("timeout")),
                      execution_mode: f.get("mode"),
                    },
                    crypto.randomUUID(),
                  );
                  setSelected(t);
                }, "Review submitted for owner approval");
              }}
            >
              <label>
                Owner device
                <select name="connection" required>
                  {nodes
                    .filter((n) => n.owner_id !== me.user_id)
                    .map((n) => (
                      <option key={n.connection_id} value={n.connection_id}>
                        {n.device_label} · {n.online ? "online" : "offline"}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                Public GitHub repository
                <input
                  name="repo"
                  type="url"
                  required
                  placeholder="https://github.com/octocat/Hello-World"
                />
              </label>
              <label>
                Pinned commit
                <input
                  name="sha"
                  required
                  pattern="[a-fA-F0-9]{40}"
                  maxLength={40}
                  placeholder="Full 40-character commit SHA"
                />
              </label>
              <label>
                Review instructions
                <textarea
                  name="prompt"
                  required
                  maxLength={12000}
                  placeholder="What should your friend’s device review?"
                />
              </label>
              <div className="form-row">
                <label>
                  Hard timeout (seconds)
                  <input
                    name="timeout"
                    type="number"
                    min="10"
                    max="60"
                    defaultValue="60"
                    required
                  />
                </label>
                <label>
                  Execution
                  <select name="mode">
                    <option value="SIMULATED">Simulated</option>
                    <option
                      value="LIVE"
                      disabled={!group?.authorization?.live_execution_enabled}
                    >
                      LIVE · uses owner’s plan
                    </option>
                  </select>
                </label>
              </div>
              <button
                disabled={
                  busy ||
                  !active ||
                  !nodes.some((n) => n.owner_id !== me.user_id)
                }
              >
                Request review <span>↗</span>
              </button>
              <small>
                Every LIVE task needs explicit owner approval. No token
                redistribution.
              </small>
            </form>
          </section>
        </div>
        <section>
          <div className="section-head">
            <h2>Your task history</h2>
            <button
              className="quiet"
              disabled={busy}
              onClick={() => void act(refresh, "Updated")}
            >
              Refresh
            </button>
          </div>
          {!taskHistory.length && (
            <p className="empty">No participant tasks yet.</p>
          )}
          {taskHistory
            .filter((t) => t.group_id === gid)
            .map((t) => (
              <button
                className="task-row"
                key={t.id}
                onClick={() => setSelected(t)}
              >
                <code>{t.id.slice(0, 8)}</code>
                <span>
                  {t.task_type.replaceAll("_", " ")} · {t.execution_mode} ·{" "}
                  {t.owner_id === me.user_id ? "Your device" : "Your request"}
                </span>
                <span className="pill">{resultLabel(t)}</span>
              </button>
            ))}
        </section>
        {selected && (
          <section className="selected">
            <div className="section-head">
              <div>
                <p className="eyebrow">TASK {selected.id.slice(0, 8)}</p>
                <h2>
                  {resultLabel(selected)} · {selected.execution_mode}
                </h2>
              </div>
              <button className="quiet" onClick={() => setSelected(null)}>
                Close
              </button>
            </div>
            <p>
              {selected.result_summary ||
                selected.last_decision?.reasons.join(", ") ||
                "Waiting for owner approval or device execution."}
            </p>
            {selected.verification_status === "UNVERIFIED" && (
              <p className="notice">
                This result has not established semantic correctness. Read the
                platform verification report before using it.
              </p>
            )}
            <p className="muted">
              Execution: {selected.status}. Verification:{" "}
              {selected.verification_status}.
            </p>
            <p className="muted">
              VERIFIED means the catalog’s trusted checks passed; UNVERIFIED
              means correctness needs human review. FAILED and CANCELLED results
              do not publish successful worker artifacts. A passed verification
              establishes only the catalog’s trusted tests. Review correctness
              still needs human assessment.
            </p>
            {!terminal.has(selected.status) && (
              <button
                className="danger"
                disabled={busy}
                onClick={() =>
                  void act(
                    () => api(`tasks/${selected.id}/cancel`, "POST"),
                    "Cancellation requested",
                  )
                }
              >
                Cancel task
              </button>
            )}
            <div className="downloads">
              {artifacts.map((a) => (
                <button
                  className="quiet"
                  key={a.id}
                  onClick={() =>
                    void act(
                      () => download(a),
                      "SHA-256 checked; artifact downloaded",
                    )
                  }
                >
                  ↓ {a.kind} <small>{a.source}</small>
                </button>
              ))}
            </div>
            <h3>Task timeline</h3>
            <ol className="timeline" aria-label="Task timeline">
              {events.map((e) => (
                <li key={e.seq}>
                  <span className="muted">
                    #{e.seq}{" "}
                    {e.created_at
                      ? new Date(e.created_at).toLocaleTimeString()
                      : ""}
                  </span>
                  <strong>{eventLabels[e.type] || e.type}</strong>
                  <span>
                    {e.payload.status === "CANCELLING"
                      ? "Cancellation requested; waiting for device cleanup"
                      : e.payload.message}
                  </span>
                </li>
              ))}
            </ol>
            {!events.length && (
              <p className="muted">
                Loading persisted history… Automatic reconnect preserves the
                cursor.
              </p>
            )}
            <button
              className="quiet"
              disabled={busy}
              onClick={() =>
                void act(
                  async () => {
                    const report = await api(
                      `experimental-tasks/${selected.id}/pilot-report`,
                    );
                    const url = URL.createObjectURL(
                      new Blob([JSON.stringify(report, null, 2)], {
                        type: "application/json",
                      }),
                    );
                    const link = document.createElement("a");
                    link.href = url;
                    link.download = `pilot-${selected.id}.json`;
                    link.click();
                    setTimeout(() => URL.revokeObjectURL(url), 1000);
                  },
                  "Protocol evidence exported; physical devices and billing still require manual confirmation",
                  false,
                )
              }
            >
              Export redacted pilot evidence
            </button>
          </section>
        )}
        {metrics && (
          <section aria-label="Pilot observations">
            <p className="eyebrow">MEASURED EXPERIMENT</p>
            <h2>Pilot observations</h2>
            <p>
              {metrics.participating_devices} participating owner devices ·{" "}
              {metrics.online_devices} online ·{" "}
              {metrics.available_owner_devices} idle and sharing. Availability
              is a heartbeat snapshot, not a quota or assignment guarantee.
            </p>
            {(["LIVE", "SIMULATED"] as const).map((mode) => {
              const m = metrics.modes[mode];
              return (
                <div key={mode}>
                  <h3>
                    {mode}{" "}
                    {mode === "SIMULATED"
                      ? "· test protocol only"
                      : "· device-reported observations"}
                  </h3>
                  <dl className="pilot-metrics">
                    <div>
                      <dt>Submitted / completed / failed / cancelled</dt>
                      <dd>
                        {m.submitted} / {m.completed} / {m.failed} /{" "}
                        {m.cancelled}
                      </dd>
                    </div>
                    <div>
                      <dt>Completion rate · terminal tasks</dt>
                      <dd>
                        {m.success_rate === null
                          ? "Not observed"
                          : `${Math.round(m.success_rate * 100)}% of ${m.success_rate_denominator}`}
                      </dd>
                    </div>
                    <div>
                      <dt>Total execution</dt>
                      <dd>
                        {m.total_execution_ms === null
                          ? "Not observed"
                          : `${(m.total_execution_ms / 1000).toFixed(1)} s`}
                      </dd>
                    </div>
                    <div>
                      <dt>Input / output tokens observed</dt>
                      <dd>
                        {m.observed_input_tokens ?? "Unknown"} /{" "}
                        {m.observed_output_tokens ?? "Unknown"}
                      </dd>
                    </div>
                    <div>
                      <dt>Average completed latency</dt>
                      <dd>
                        {m.average_completed_latency_ms === null
                          ? "Not observed"
                          : `${(m.average_completed_latency_ms / 1000).toFixed(1)} s`}
                      </dd>
                    </div>
                    <div>
                      <dt>Usage-limit errors</dt>
                      <dd>{m.usage_limit_errors}</dd>
                    </div>
                  </dl>
                </div>
              );
            })}
            <p className="muted">
              Completion measures execution and handling, not semantic
              correctness. No exact remaining subscription quota or financial
              savings are inferred. Protocol counters cannot prove a real
              two-device experiment. Confirm devices and funding in the
              controlled runbook.
            </p>
          </section>
        )}
        <section id="history">
          <p className="eyebrow">TRACEABLE DECISIONS</p>
          <h2>Experiment audit history</h2>
          {!audit.length ? (
            <p className="empty">No dispatch decisions yet.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Task</th>
                    <th>Mode</th>
                    <th>Decision</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.map((a) => (
                    <tr key={a.id}>
                      <td>{new Date(a.created_at).toLocaleString()}</td>
                      <td>
                        <code>{a.task_id.slice(0, 8)}</code>
                      </td>
                      <td>{a.execution_mode}</td>
                      <td>{a.decision}</td>
                      <td>
                        {a.reasons.join(", ") || "All dispatch gates passed"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
        <footer>
          SpareAgent · Private friends beta{" "}
          <span>
            Exact subscription quota is not available through this integration.
          </span>
        </footer>
      </main>
    </div>
  );
}
