"use client";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { initialLang, Lang, saveLang, Translate, translator } from "./i18n";
import { limitsUpdate } from "../lib/preferences.mjs";

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
  max_task_duration_seconds: number;
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
  auto_approve_members?: boolean;
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
  // 0011: null for kinds that take no repository (e.g. TRANSLATE_DOCUMENT)
  repository_url: string | null;
  commit_sha: string | null;
  attachments?: { id: string; filename: string; size_bytes: number }[];
  max_duration_seconds: number;
  approval_status: string;
  approval_source: string | null;
  owner_id: string;
  verification_status: string;
  result_summary: string | null;
  last_decision: null | { reasons: string[] };
  kind?: string;
  requirement?: string;
  parameters?: Record<string, string>;
  deliverable?: { format: string; filename: string } | null;
  progress?: { percent: number | null; message: string | null } | null;
  queue_position?: number | null;
  failure?: { code: string | null; category: string | null } | null;
};
type TaskKind = {
  kind: string;
  name_zh: string;
  name_en: string;
  description_zh: string;
  status: string;
  needs_repository: boolean;
  min_attachments: number;
  max_attachments: number;
  extensions: string[];
  required_parameters: string[];
  deliverable_format: string;
  deliverable_formats: string[];
  live_available: boolean;
};
const MAX_UPLOAD = 20 * 1024 * 1024;
const LANGUAGES = ["zh-Hans", "en", "ja", "ko", "fr", "de", "es", "ru"];
async function uploadFile(f: File): Promise<string> {
  if (f.size > MAX_UPLOAD) throw new Error(`${f.name}: > 20 MB`);
  const r = await fetch("/api/experimental-uploads", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/octet-stream",
      "X-Filename": encodeURIComponent(f.name),
    },
    body: f,
  });
  const out = await r.json().catch(() => null);
  if (!r.ok) throw new Error(out?.error?.message || `Upload failed (${r.status})`);
  return out.id as string;
}
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
// Fixed device progress lines in Chinese; Codex's own free-text updates are summarised.
const PROGRESS_ZH: [RegExp, string][] = [
  [/^Prepared (\d+) attachment/, "已准备 $1 个附件"],
  [/^Verified local ChatGPT login/, "已确认本机 ChatGPT 登录（凭据只在设备上）"],
  [/^Checked out the pinned commit/, "已取出指定版本的代码"],
  [/^Fetching pinned commit/, "正在获取指定版本的代码"],
  [/^Applying allowlisted modification/, "正在应用改动"],
  [/^Running trusted tests/, "正在沙箱中运行测试"],
  [/^Tests finished/, "测试完成，正在打包结果"],
  [/^Checking the result/, "正在检查结果"],
  [/^Result ready/, "结果已就绪"],
  [/^Codex run complete/, "Codex 已完成"],
  [/^Loaded task metadata \(mock/, "模拟：已读取任务信息"],
  [/^Generated deterministic fixture/, "模拟：已生成占位结果"],
];
function progressText(
  message: string | null | undefined,
  lang: string,
  codexLabel: string,
): string {
  if (!message || lang !== "zh") return message || "";
  for (const [re, zh] of PROGRESS_ZH) {
    const m = message.match(re);
    if (m) return zh.replace("$1", m[1] || "");
  }
  return codexLabel;
}
function resultLabel(t: Task): string {
  if (t.status === "CANCELLED" || t.status === "CANCELLING") return t.status;
  if (t.status === "TIMED_OUT") return "TIMED_OUT";
  if (
    t.status === "FAILED" ||
    ["FAILED", "ERROR"].includes(t.verification_status)
  )
    return "FAILED";
  if (t.status === "SUCCEEDED")
    return t.verification_status === "PASSED" ? "VERIFIED" : "UNVERIFIED";
  return t.status;
}
type Artifact = {
  id: string;
  kind: string;
  source: string;
  sha256: string;
  name?: string | null;
};
const terminal = new Set(["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"]);
// Must match the server's EXPERIMENT_TERMS_VERSION; the API rejects a mismatch.
const TERMS_VERSION = "2026-10-experimental-v1";
const eventKeys: Record<string, true> = Object.fromEntries(
  [
    "created",
    "queued",
    "reserved",
    "started",
    "progress",
    "verifying",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
    "retrying",
  ].map((x) => [`task.${x}`, true]),
);
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
  t,
}: {
  names: string[];
  values: Record<string, boolean>;
  set: (x: Record<string, boolean>) => void;
  t: Translate;
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
          {t(`ack.${k}` as never)}
        </label>
      ))}
    </div>
  );
}
export default function Dashboard() {
  const [lang, setLang] = useState<Lang>("zh");
  useEffect(() => setLang(initialLang()), []);
  useEffect(() => {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  }, [lang]);
  const t = translator(lang);
  // A code's label when one exists, else the code itself (never a raw "prefix.KEY").
  const lbl = (prefix: string, value: string) => {
    const key = `${prefix}.${value}`;
    const out = t(key as never);
    return out === key ? value : out;
  };
  const switchLang = () => {
    const next: Lang = lang === "zh" ? "en" : "zh";
    setLang(next);
    saveLang(next);
  };
  const [loginTab, setLoginTab] = useState<"signin" | "join">("signin"),
    [joinCode, setJoinCode] = useState(""),
    [joinAcksLogin, setJoinAcksLogin] = useState<Record<string, boolean>>({});
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
  const [kinds, setKinds] = useState<TaskKind[]>([]),
    [kindSel, setKindSel] = useState(""),
    [files, setFiles] = useState<File[]>([]);
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
    const [gs, ds, us, ts, ins, ks] = await Promise.all([
      api<Group[]>("experiments/groups"),
      api<{ items: Device[] }>("subscriptions/status"),
      api<{ items: Usage[] }>("subscriptions/usage"),
      api<{ items: Task[] }>("experimental-tasks"),
      api<{ items: Task[] }>("experimental-tasks?role=owner"),
      api<{ items: TaskKind[] }>("task-kinds"),
    ]);
    setKinds(ks.items.filter((k) => k.status !== "DISABLED"));
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
    // Links from the one-click app: #login=<one-time code> or #join=<group invite code>.
    const hash = new URLSearchParams(window.location.hash.slice(1));
    const login = hash.get("login"),
      join = hash.get("join");
    if (login || join)
      window.history.replaceState(null, "", window.location.pathname);
    if (join) {
      setJoinCode(join);
      setLoginTab("join");
    }
    const start = login
      ? api<Me>("friends/login", "POST", { token: login })
      : api<Me>("friends/me");
    start
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
    message = t("saved"),
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
        setNotice(t("stream_reconnect"));
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
    link.download =
      a.name ||
      `${a.kind.toLowerCase()}-${a.id}${a.kind === "VERIFICATION_REPORT" ? ".json" : ""}`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const kindName = (x: Task) => {
    const k = kinds.find((k) => k.kind === (x.kind || x.task_type));
    return k ? (lang === "zh" ? k.name_zh : k.name_en) : x.task_type.replaceAll("_", " ");
  };
  const statusLine = (x: Task): string => {
    if (x.status === "QUEUED") {
      const waits = (x.last_decision?.reasons || [])
        .map((r) => t(`wait.${r}` as never))
        .filter((r) => !r.startsWith("wait."));
      const head = x.queue_position
        ? t("st_queued_n", { n: x.queue_position })
        : t("st_queued");
      return waits.length ? `${head} · ${waits.join("；")}` : head;
    }
    if (["RESERVED", "RUNNING"].includes(x.status)) {
      const p = x.progress;
      return p?.percent != null
        ? t("st_running_p", { p: p.percent, m: progressText(p.message, lang, t("prog.codex")) })
        : t("st_running");
    }
    if (x.status === "RETRY_PENDING") return t("st_retrying");
    if (x.status === "VERIFYING") return t("st_verifying");
    if (x.failure && ["FAILED", "TIMED_OUT"].includes(x.status))
      return `${t(`fail.${x.failure.category || "OTHER"}` as never)}${x.failure.code ? ` (${x.failure.code})` : ""}`;
    if (x.status === "SUCCEEDED")
      return x.verification_status === "PASSED"
        ? t("st_verified")
        : t("st_done_unverified");
    return lbl("res", resultLabel(x));
  };
  const taskHistory = Array.from(
    new Map([...tasks, ...incoming].map((t) => [t.id, t])).values(),
  );
  if (loading)
    return (
      <main className="login">
        <p>{t("opening")}</p>
      </main>
    );
  if (!me)
    return (
      <main className="login">
        <div className="mark">
          S<span>•</span>
        </div>
        <p className="eyebrow">{t("login_eyebrow")}</p>
        <h1>
          {t("hero_1")}
          <br />
          {t("hero_2")}
        </h1>
        <div className="tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={loginTab === "signin"}
            className={loginTab === "signin" ? "" : "quiet"}
            onClick={() => setLoginTab("signin")}
          >
            {t("tab_signin")}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={loginTab === "join"}
            className={loginTab === "join" ? "" : "quiet"}
            onClick={() => setLoginTab("join")}
          >
            {t("tab_join")}
          </button>
        </div>
        {loginTab === "signin" ? (
          <>
            <p>{t("login_intro")}</p>
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
                }, t("signed_in"));
              }}
            >
              <label>
                {t("invitation_code")}
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
                {t("enter")} <span>↗</span>
              </button>
            </form>
          </>
        ) : (
          <>
            <p>{t("join_intro")}</p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void act(async () => {
                  setMe(
                    await api<Me>("friends/join-code", "POST", {
                      code: joinCode.trim(),
                      terms_version: TERMS_VERSION,
                      acknowledgements: joinAcksLogin,
                    }),
                  );
                }, t("joined_group"));
              }}
            >
              <label>
                {t("group_invite_code")}
                <input
                  value={joinCode}
                  onChange={(e) => setJoinCode(e.target.value)}
                  autoComplete="off"
                  required
                  minLength={12}
                  maxLength={32}
                  placeholder="ABCD-EFGH-JKLM"
                />
              </label>
              <Checks
                names={joinAcks}
                values={joinAcksLogin}
                set={setJoinAcksLogin}
                t={t}
              />
              <button
                disabled={busy || !joinAcks.every((k) => joinAcksLogin[k])}
              >
                {t("join_button")} <span>↗</span>
              </button>
            </form>
          </>
        )}
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <small>{t("login_footer")}</small>
        <button className="quiet lang" type="button" onClick={switchLang}>
          {t("lang_toggle")}
        </button>
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
        <p className="eyebrow">FRIENDS BETA / 07</p>
        <nav>
          <a href="#overview">{t("nav_overview")}</a>
          <a href="#devices">{t("nav_devices")}</a>
          <a href="#requests">
            {t("nav_requests")}{" "}
            <b>
              {
                incoming.filter(
                  (t) =>
                    t.approval_status === "PENDING" && !terminal.has(t.status),
                ).length
              }
            </b>
          </a>
          <a href="#submit">{t("nav_submit")}</a>
          <a href="#history">{t("nav_history")}</a>
        </nav>
        <div className="aside-bottom">
          <span className="dot" /> {t("private_experiment")}
          <p>
            {t("aside_1")}
            <br />
            {t("aside_2")}
          </p>
          <button className="quiet" type="button" onClick={switchLang}>
            {t("lang_toggle")}
          </button>
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
                t("signed_out"),
                false,
              )
            }
          >
            {t("sign_out")}
          </button>
        </div>
      </aside>
      <main>
        <header>
          <div>
            <p className="eyebrow">{t("ws_eyebrow")}</p>
            <h1>{t("ws_title")}</h1>
            <p>{t("ws_sub")}</p>
          </div>
          <span className="pill">
            {me.live_enabled ? t("live_on") : t("live_off")}
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
              {t("group_label")}
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
              <p>{t("no_groups")}</p>
            )}
            <span className="muted">
              {t("participants", {
                n: group?.members.filter((m) => m.status === "ACTIVE").length || 0,
                max: group?.max_members || 5,
              })}
            </span>
          </div>
          <div>
            <p className="eyebrow">{t("authorization")}</p>
            <strong>
              {group?.authorization?.authorization_reference || t("not_recorded")}
            </strong>
            <p className="muted">
              {group?.authorization
                ? t("expires", {
                    date: new Date(group.authorization.expires_at).toLocaleDateString(),
                  })
                : t("operator_review")}
            </p>
          </div>
        </section>
        {group && !active && (
          <section>
            <h2>{t("accept_invitation")}</h2>
            <Checks names={joinAcks} values={joinChecks} set={setJoinChecks} t={t} />
            <button
              disabled={busy || !joinAcks.every((k) => joinChecks[k])}
              onClick={() =>
                void act(
                  () =>
                    api(`experiments/groups/${gid}/join`, "POST", {
                      terms_version: me.terms_version,
                      acknowledgements: joinChecks,
                    }),
                  t("joined_group"),
                )
              }
            >
              {t("join_named", { name: group.name })}
            </button>
          </section>
        )}
        <div className="stats">
          <article>
            <span>{t("stat_devices")}</span>
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
            <small>{t("stat_devices_sub")}</small>
          </article>
          <article>
            <span>{t("stat_tasks")}</span>
            <strong>
              {usage
                .reduce((n, u) => n + u.tasks_started_last_24h, 0)
                .toString()
                .padStart(2, "0")}
            </strong>
            <small>{t("stat_tasks_sub")}</small>
          </article>
          <article>
            <span>{t("stat_plan")}</span>
            <strong className="unavailable">{t("not_available")}</strong>
            <small>{t("check_usage")}</small>
          </article>
        </div>
        <section id="devices">
          <div className="section-head">
            <div>
              <p className="eyebrow">{t("owner_controls")}</p>
              <h2>{t("your_devices")}</h2>
            </div>
            <span className="muted">{t("one_at_time")}</span>
          </div>
          {!devices.length && (
            <p className="empty">{t("no_device")}</p>
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
                    {fresh ? d.worker_status : t("offline_stale")} · ChatGPT{" "}
                    {d.connection.auth_state} ·{" "}
                    {d.preferences?.sharing_state || t("not_sharing")}
                    {d.preferences?.auto_approve_members ? ` · ${t("auto_on")}` : ""}
                  </p>
                  <small>
                    {d.last_heartbeat
                      ? t("last_seen", {
                          time: new Date(d.last_heartbeat).toLocaleString(),
                        })
                      : t("no_heartbeat")}
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
                        t("paused_msg"),
                      )
                    }
                  >
                    {t("pause")}
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
                        t("revoked_msg"),
                      )
                    }
                  >
                    {t("revoke")}
                  </button>
                </div>
                <div className="device-detail">
                  <span>
                    {t("tasks_rolling", {
                      n: u?.tasks_started_last_24h || 0,
                      max: d.preferences?.max_tasks_per_day ?? "—",
                    })}
                  </span>
                  <span>
                    {t("tokens_observed", {
                      i: u?.last_observation_at ? u.observed_input_tokens : t("unknown"),
                      o: u?.last_observation_at ? u.observed_output_tokens : t("unknown"),
                    })}
                  </span>
                  <span>{t("capacity", { c: u?.capacity_state || "UNKNOWN" })}</span>
                  {u?.reset_hint && <span>{u.reset_hint}</span>}
                </div>
                <details>
                  <summary>{t("configure")}</summary>
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      const f = new FormData(e.currentTarget);
                      void act(async () => {
                        await api(
                          "sharing/preferences",
                          "PUT",
                          limitsUpdate(d.connection.id, gid, f),
                        );
                        await api("sharing/enable", "POST", {
                          connection_id: d.connection.id,
                          terms_version: me.terms_version,
                          acknowledgements: shareChecks,
                        });
                      }, t("enabled_msg"));
                    }}
                  >
                    <div className="form-row">
                      <label>
                        {t("daily_limit")}
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
                        {t("hard_timeout")}
                        <input
                          name="duration"
                          type="number"
                          min="10"
                          max={group?.max_task_duration_seconds ?? 600}
                          defaultValue={
                            d.preferences?.max_task_duration_seconds ?? 600
                          }
                          required
                        />
                      </label>
                    </div>
                    <Checks
                      names={sharingAcks}
                      values={shareChecks}
                      set={setShareChecks}
                      t={t}
                    />
                    <button
                      disabled={
                        busy ||
                        !active ||
                        !sharingAcks.every((k) => shareChecks[k])
                      }
                    >
                      {t("enable_resume")}
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
                        t("attest_msg"),
                      );
                    }}
                  >
                    <h3>{t("local_statements")}</h3>
                    <label>
                      {t("actual_check")}
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
                      {t("no_credits")}
                    </label>
                    <label className="check">
                      <input name="reload" type="checkbox" />
                      {t("reload_off")}
                    </label>
                    <button className="quiet" disabled={busy}>
                      {t("record_attest")}
                    </button>
                    <small>{t("attest_note")}</small>
                  </form>
                </details>
              </article>
            );
          })}
          <details>
            <summary>{t("connect_mac")}</summary>
            <p className="notice">{t("one_click_tip")}</p>
            <p>{t("setup_intro")}</p>
            <ol className="setup-steps">
              <li>
                {t("step_1")}
                <code>
                  bash setup-worker-macos.sh
                  ./spareagent_worker-0.7.0-py3-none-any.whl SHA256
                </code>
              </li>
              <li>
                {t("step_2_pre")} <code>codex login</code> {t("step_2_post")}
              </li>
              <li>{t("step_3")}</li>
              <li>{t("step_4")}</li>
              <li>
                {t("step_5_pre")} <code>spareagent-worker pilot-check</code>
                {t("step_5_post")}
              </li>
            </ol>
            <p className="notice">{t("live_starts_off")}</p>
            <p>
              <a
                href="https://github.com/constana-an/spareagent/blob/main/docs/WEEK6_PILOT_RUNBOOK.md"
                target="_blank"
                rel="noreferrer"
              >
                {t("runbook")}
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
                  t("token_created"),
                )
              }
            >
              {t("create_token")}
            </button>
            {enrollment && (
              <div className="notice">
                <p>
                  {t("resource")} <code>{enrollment.resource_id}</code>
                </p>
                <label>
                  {t("private_token", {
                    time: new Date(enrollment.expires_at).toLocaleTimeString(),
                  })}
                  <textarea readOnly value={enrollment.token} />
                </label>
                <p>
                  {t("use_token_pre")} <code>spareagent-worker register</code>{" "}
                  {t("use_token_post")}
                </p>
                <button className="quiet" onClick={() => setEnrollment(null)}>
                  {t("hide_token")}
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
                  t("device_connected"),
                );
              }}
            >
              <label>
                {t("registered_worker")}
                <input
                  name="worker"
                  required
                  pattern="[0-9a-fA-F-]{36}"
                  placeholder={t("worker_placeholder")}
                />
              </label>
              <label>
                {t("device_name")}
                <input
                  name="label"
                  maxLength={80}
                  required
                  placeholder={t("device_placeholder")}
                />
              </label>
              <label className="check">
                <input type="checkbox" required />
                {t("verified_login")}
              </label>
              <button disabled={busy || !active}>{t("connect_device")}</button>
            </form>
          </details>
        </section>
        <div className="columns">
          <section id="requests">
            <p className="eyebrow">{t("permission_first")}</p>
            <h2>{t("incoming")}</h2>
            <p className="muted">{t("incoming_note")}</p>
            {!incoming.filter((t) => t.group_id === gid).length && (
              <p className="empty">{t("quiet")}</p>
            )}
            {incoming
              .filter((x) => x.group_id === gid)
              .map((x) => (
                <article className="request" key={x.id}>
                  <div className="section-head">
                    <h3>{x.task_type.replaceAll("_", " ")}</h3>
                    <span className="pill">{x.execution_mode}</span>
                  </div>
                  <p>{x.prompt}</p>
                  <p className="muted break">
                    {x.repository_url ? (
                      <>
                        {x.repository_url}
                        <br />
                        <code>{x.commit_sha}</code>
                        <br />
                      </>
                    ) : null}
                    {(x.attachments ?? []).map((a) => (
                      <span key={a.id}>
                        {a.filename}
                        <br />
                      </span>
                    ))}
                    {t("hard_timeout_n", { n: x.max_duration_seconds })}
                  </p>
                  <div className="actions">
                    {x.approval_status === "PENDING" &&
                      !terminal.has(x.status) && (
                        <>
                          <button
                            disabled={busy}
                            onClick={() =>
                              void act(
                                () =>
                                  api(
                                    `experimental-tasks/${x.id}/approve`,
                                    "POST",
                                  ),
                                t("task_approved"),
                              )
                            }
                          >
                            {t("approve_task")}
                          </button>
                          <button
                            className="quiet"
                            disabled={busy}
                            onClick={() =>
                              void act(
                                () =>
                                  api(
                                    `experimental-tasks/${x.id}/reject`,
                                    "POST",
                                  ),
                                t("task_rejected"),
                              )
                            }
                          >
                            {t("reject")}
                          </button>
                        </>
                      )}
                    <button className="quiet" onClick={() => setSelected(x)}>
                      {x.approval_source === "OWNER_STANDING_APPROVAL"
                        ? t("auto_approved")
                        : `${t("inspect")} · ${x.approval_status.toLowerCase()}`}
                    </button>
                  </div>
                </article>
              ))}
          </section>
          <section id="submit">
            <p className="eyebrow">{t("ask_circle")}</p>
            <h2>{t("submit_review")}</h2>
            {(() => {
              const kind =
                kinds.find((k) => k.kind === kindSel) || kinds[0] || null;
              const kindName = (k: TaskKind) =>
                lang === "zh" ? k.name_zh : k.name_en;
              return (
                <form
                  onSubmit={(e: FormEvent<HTMLFormElement>) => {
                    e.preventDefault();
                    if (!kind) return;
                    const f = new FormData(e.currentTarget);
                    void act(async () => {
                      const ids: string[] = [];
                      for (const file of files) {
                        setNotice(t("uploading", { name: file.name }));
                        ids.push(await uploadFile(file));
                      }
                      const target = f.get("target_language");
                      const fmt = f.get("format");
                      const created = await api<Task>(
                        "experimental-tasks",
                        "POST",
                        {
                          kind: kind.kind,
                          group_id: gid,
                          connection_id: f.get("connection"),
                          requirement: f.get("prompt"),
                          max_duration_seconds: Number(f.get("timeout")),
                          execution_mode: f.get("mode"),
                          attachments: ids,
                          parameters: target ? { target_language: target } : {},
                          ...(fmt ? { deliverable: { format: fmt } } : {}),
                          ...(kind.needs_repository
                            ? {
                                repository: {
                                  url: f.get("repo"),
                                  commit_sha: f.get("sha"),
                                },
                              }
                            : {}),
                        },
                        crypto.randomUUID(),
                      );
                      setFiles([]);
                      setSelected(created);
                    }, t("submitted"));
                  }}
                >
                  <label>
                    {t("kind_label")}
                    <select
                      value={kind?.kind || ""}
                      onChange={(e) => {
                        setKindSel(e.target.value);
                        setFiles([]);
                      }}
                    >
                      {kinds.map((k) => (
                        <option key={k.kind} value={k.kind}>
                          {kindName(k)}
                          {k.status === "OPEN" ? "" : ` · ${t("kind_sim_only")}`}
                        </option>
                      ))}
                    </select>
                  </label>
                  {kind && <small>{lang === "zh" ? kind.description_zh : kind.name_en}</small>}
                  <label>
                    {t("owner_device")}
                    <select name="connection" required>
                      {nodes
                        .filter((n) => n.owner_id !== me.user_id)
                        .map((n) => (
                          <option key={n.connection_id} value={n.connection_id}>
                            {n.device_label} ·{" "}
                            {n.online ? t("online") : t("offline")}
                            {n.requires_approval ? "" : ` · ${t("auto_label")}`}
                          </option>
                        ))}
                    </select>
                  </label>
                  {kind?.needs_repository && (
                    <>
                      <label>
                        {t("public_repo")}
                        <input
                          name="repo"
                          type="url"
                          required
                          placeholder="https://github.com/octocat/Hello-World"
                        />
                      </label>
                      <label>
                        {t("pinned_commit")}
                        <input
                          name="sha"
                          required
                          pattern="[a-fA-F0-9]{40}"
                          maxLength={40}
                          placeholder={t("sha_placeholder")}
                        />
                      </label>
                    </>
                  )}
                  {!!kind?.max_attachments && (
                    <label>
                      {t("attachments")}
                      <input
                        type="file"
                        multiple={kind.max_attachments > 1}
                        accept={kind.extensions.map((x) => "." + x).join(",")}
                        onChange={(e) =>
                          setFiles(
                            Array.from(e.target.files || []).slice(
                              0,
                              kind.max_attachments,
                            ),
                          )
                        }
                        required={kind.min_attachments > 0}
                      />
                      <small>
                        {t("attachments_hint", {
                          types: kind.extensions.map((x) => "." + x).join(" "),
                          n: kind.max_attachments,
                        })}
                        {kind.min_attachments === 0 ? t("attachments_optional") : ""}
                      </small>
                    </label>
                  )}
                  {kind?.required_parameters.includes("target_language") && (
                    <label>
                      {t("target_language")}
                      <select name="target_language" defaultValue="en">
                        {LANGUAGES.map((l) => (
                          <option key={l} value={l}>
                            {t(`lang.${l}` as never)}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  {kind && kind.deliverable_formats.length > 1 && (
                    <label>
                      {t("deliverable_format")}
                      <select name="format" defaultValue={kind.deliverable_format}>
                        {kind.deliverable_formats.map((x) => (
                          <option key={x} value={x}>
                            .{x}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  <label>
                    {t("review_instructions")}
                    <textarea
                      name="prompt"
                      required
                      maxLength={12000}
                      placeholder={t("prompt_placeholder")}
                    />
                  </label>
                  <div className="form-row">
                    <label>
                      {t("hard_timeout")}
                      <input
                        name="timeout"
                        type="number"
                        min="10"
                        max={group?.max_task_duration_seconds ?? 600}
                        defaultValue="600"
                        required
                      />
                    </label>
                    <label>
                      {t("execution")}
                      <select name="mode">
                        <option value="SIMULATED">{t("simulated")}</option>
                        <option
                          value="LIVE"
                          disabled={
                            !group?.authorization?.live_execution_enabled ||
                            !kind?.live_available
                          }
                        >
                          {t("live_option")}
                        </option>
                      </select>
                    </label>
                  </div>
                  <button
                    disabled={
                      busy ||
                      !active ||
                      !kind ||
                      !nodes.some((n) => n.owner_id !== me.user_id)
                    }
                  >
                    {t("request_review")} <span>↗</span>
                  </button>
                  <small>{t("submit_note")}</small>
                </form>
              );
            })()}
          </section>
        </div>
        <section>
          <div className="section-head">
            <h2>{t("history")}</h2>
            <button
              className="quiet"
              disabled={busy}
              onClick={() => void act(refresh, t("updated"))}
            >
              {t("refresh")}
            </button>
          </div>
          {!taskHistory.length && (
            <p className="empty">{t("no_tasks")}</p>
          )}
          {taskHistory
            .filter((x) => x.group_id === gid)
            .map((x) => (
              <button
                className="task-row"
                key={x.id}
                onClick={() => setSelected(x)}
              >
                <code>{x.id.slice(0, 8)}</code>
                <span>
                  {kindName(x)} · {x.execution_mode} ·{" "}
                  {x.owner_id === me.user_id ? t("your_device") : t("your_request")}
                </span>
                <span className="pill">{statusLine(x)}</span>
              </button>
            ))}
        </section>
        {selected && (
          <section className="selected">
            <div className="section-head">
              <div>
                <p className="eyebrow">{t("task_n", { id: selected.id.slice(0, 8) })}</p>
                <h2>
                  {kindName(selected)} · {statusLine(selected)}
                </h2>
              </div>
              <button className="quiet" onClick={() => setSelected(null)}>
                {t("close")}
              </button>
            </div>
            {selected.progress?.percent != null &&
              ["RESERVED", "RUNNING"].includes(selected.status) && (
                <progress max={100} value={selected.progress.percent} />
              )}
            {selected.failure &&
              ["FAILED", "TIMED_OUT"].includes(selected.status) && (
                <p className="error">
                  {t(`fail_hint.${selected.failure.category || "OTHER"}` as never)}
                </p>
              )}
            <p>{selected.result_summary || t("waiting")}</p>
            {selected.verification_status === "UNVERIFIED" && (
              <p className="notice">{t("unverified_note")}</p>
            )}
            <p className="muted">
              {t("exec_verif", {
                s: lbl("res", selected.status),
                v: lbl("ver", selected.verification_status),
              })}
            </p>
            <p className="muted">{t("verified_explain")}</p>
            {!terminal.has(selected.status) && (
              <button
                className="danger"
                disabled={busy}
                onClick={() =>
                  void act(
                    () => api(`tasks/${selected.id}/cancel`, "POST"),
                    t("cancel_requested"),
                  )
                }
              >
                {t("cancel_task")}
              </button>
            )}
            {artifacts.some((a) => a.kind === "DELIVERABLE") && (
              <>
                <h3>{t("deliverables")}</h3>
                <div className="downloads">
                  {artifacts
                    .filter((a) => a.kind === "DELIVERABLE")
                    .map((a) => (
                      <button
                        key={a.id}
                        onClick={() => void act(() => download(a), t("sha_checked"))}
                      >
                        ↓ {a.name}
                      </button>
                    ))}
                </div>
              </>
            )}
            <div className="downloads">
              {artifacts
                .filter((a) => a.kind !== "DELIVERABLE")
                .map((a) => (
                  <button
                    className="quiet"
                    key={a.id}
                    onClick={() => void act(() => download(a), t("sha_checked"))}
                  >
                    ↓ {lbl("art", a.kind)} <small>{lbl("src", a.source)}</small>
                  </button>
                ))}
            </div>
            <h3>{t("timeline")}</h3>
            <ol className="timeline" aria-label="Task timeline">
              {events.map((e) => (
                <li key={e.seq}>
                  <span className="muted">
                    #{e.seq}{" "}
                    {e.created_at
                      ? new Date(e.created_at).toLocaleTimeString()
                      : ""}
                  </span>
                  <strong>
                    {e.type in eventKeys ? t(`event.${e.type}` as never) : e.type}
                  </strong>
                  <span>
                    {e.payload.status === "CANCELLING"
                      ? t("cancelling_wait")
                      : e.type === "task.progress"
                        ? progressText(e.payload.message, lang, t("prog.codex"))
                        : e.payload.message}
                  </span>
                </li>
              ))}
            </ol>
            {!events.length && (
              <p className="muted">{t("loading_history")}</p>
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
                  t("exported"),
                  false,
                )
              }
            >
              {t("export_evidence")}
            </button>
          </section>
        )}
        {metrics && (
          <section aria-label="Pilot observations">
            <p className="eyebrow">{t("measured")}</p>
            <h2>{t("pilot_obs")}</h2>
            <p>
              {t("metrics_line", {
                a: metrics.participating_devices,
                b: metrics.online_devices,
                c: metrics.available_owner_devices,
              })}
            </p>
            {(["LIVE", "SIMULATED"] as const).map((mode) => {
              const m = metrics.modes[mode];
              return (
                <div key={mode}>
                  <h3>
                    {mode}{" "}
                    {mode === "SIMULATED"
                      ? t("test_protocol")
                      : t("device_reported")}
                  </h3>
                  <dl className="pilot-metrics">
                    <div>
                      <dt>{t("m_counts")}</dt>
                      <dd>
                        {m.submitted} / {m.completed} / {m.failed} /{" "}
                        {m.cancelled}
                      </dd>
                    </div>
                    <div>
                      <dt>{t("m_rate")}</dt>
                      <dd>
                        {m.success_rate === null
                          ? t("not_observed")
                          : t("rate_of", {
                              p: Math.round(m.success_rate * 100),
                              n: m.success_rate_denominator,
                            })}
                      </dd>
                    </div>
                    <div>
                      <dt>{t("m_exec")}</dt>
                      <dd>
                        {m.total_execution_ms === null
                          ? t("not_observed")
                          : `${(m.total_execution_ms / 1000).toFixed(1)} s`}
                      </dd>
                    </div>
                    <div>
                      <dt>{t("m_tokens")}</dt>
                      <dd>
                        {m.observed_input_tokens ?? t("unknown")} /{" "}
                        {m.observed_output_tokens ?? t("unknown")}
                      </dd>
                    </div>
                    <div>
                      <dt>{t("m_latency")}</dt>
                      <dd>
                        {m.average_completed_latency_ms === null
                          ? t("not_observed")
                          : `${(m.average_completed_latency_ms / 1000).toFixed(1)} s`}
                      </dd>
                    </div>
                    <div>
                      <dt>{t("m_limit_errors")}</dt>
                      <dd>{m.usage_limit_errors}</dd>
                    </div>
                  </dl>
                </div>
              );
            })}
            <p className="muted">{t("metrics_note")}</p>
          </section>
        )}
        <section id="history">
          <p className="eyebrow">{t("traceable")}</p>
          <h2>{t("audit_h")}</h2>
          {!audit.length ? (
            <p className="empty">{t("no_decisions")}</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("th_time")}</th>
                    <th>{t("th_task")}</th>
                    <th>{t("th_mode")}</th>
                    <th>{t("th_decision")}</th>
                    <th>{t("th_reason")}</th>
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
                        {a.reasons.join(", ") || t("all_gates")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
        <footer>
          {t("footer")} <span>{t("footer_note")}</span>
        </footer>
      </main>
    </div>
  );
}
