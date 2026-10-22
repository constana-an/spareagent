// Helpers for the member's "new task" form and task list, kept out of page.tsx so
// they can be tested with node --test.

export const MAX_UPLOAD = 20 * 1024 * 1024;

// Files picked, dropped or pasted all pass through here, so the kind's type list,
// the 20 MB cap and the attachment count apply the same way. The same file added
// twice is kept once.
export function mergeAttachments(current, incoming, kind) {
  const files = [...current],
    rejected = [];
  for (const f of incoming) {
    const ext = f.name.includes(".") ? f.name.split(".").pop().toLowerCase() : "";
    if (!kind.extensions.includes(ext)) rejected.push({ name: f.name, reason: "type" });
    else if (f.size > MAX_UPLOAD) rejected.push({ name: f.name, reason: "size" });
    else if (files.some((x) => x.name === f.name && x.size === f.size)) continue;
    else if (files.length >= kind.max_attachments)
      rejected.push({ name: f.name, reason: "count" });
    else files.push(f);
  }
  return { files, rejected };
}

const IMAGE_EXT = { "image/png": "png", "image/jpeg": "jpg" };

// Browsers name every pasted screenshot "image.png"; give each a distinct name
// from the paste time so several pastes don't collapse into one attachment.
export function pastedName(type, now, index = 0) {
  const p = (n) => String(n).padStart(2, "0");
  const stamp =
    `${now.getFullYear()}${p(now.getMonth() + 1)}${p(now.getDate())}-` +
    `${p(now.getHours())}${p(now.getMinutes())}${p(now.getSeconds())}`;
  return `pasted-${stamp}${index ? `-${index + 1}` : ""}.${IMAGE_EXT[type] || "png"}`;
}

// Final states a member is told about. CANCELLED is left out: the member (or the
// owner, visibly) did that themselves.
const NOTIFY = new Set(["SUCCEEDED", "FAILED", "TIMED_OUT"]);

// Compares this poll with the previous one. `previous` is null on the first poll,
// which only records statuses, so reloading the page announces nothing.
export function newlyFinished(previous, tasks) {
  const seen = new Map(tasks.map((t) => [t.id, t.status]));
  if (!previous) return { finished: [], seen };
  const finished = tasks.filter(
    (t) =>
      NOTIFY.has(t.status) && previous.has(t.id) && previous.get(t.id) !== t.status,
  );
  return { finished, seen };
}

// What "submit again" puts back into the form. Attachments can't be refilled
// (the browser no longer has the files), so their names are returned for a hint.
export function draftFrom(task) {
  return {
    kind: task.kind || task.task_type,
    prompt: task.requirement || task.prompt || "",
    repo: task.repository_url || "",
    sha: task.commit_sha || "",
    target_language: task.parameters?.target_language || "",
    format: task.deliverable?.format || "",
    network: !!task.network_access,
    timeout: task.max_duration_seconds,
    mode: task.execution_mode || "SIMULATED",
    connection: task.connection_id || "",
    attachments: (task.attachments || []).map((a) => a.filename),
  };
}

// Example requests shown under the requirement box, per task kind.
export const EXAMPLES = {
  GENERAL: {
    zh: [
      "把附件里的会议记录整理成一页纪要：做出的决定、待办（负责人和截止日期）、还没解决的问题。",
      "帮我写一封邮件，向房东说明情况并申请把这个月的房租推迟一周交，语气礼貌、简短。",
      "根据附件的数据画一张折线图（PNG），横轴是月份、纵轴是销售额，再用三句话总结趋势。",
    ],
    en: [
      "Turn the attached meeting notes into a one-page summary: decisions, action items (owner and due date), open questions.",
      "Write a short, polite email to my landlord asking to pay this month's rent one week late.",
      "Draw a line chart (PNG) from the attached data, months on the x-axis and sales on the y-axis, and sum up the trend in three sentences.",
    ],
  },
  TRANSLATE_DOCUMENT: {
    zh: [
      "保留原有的标题层级和列表格式；专有名词第一次出现时在括号里保留原文。",
      "面向普通读者，用通俗自然的说法；表格里的数字和单位不要改动。",
    ],
    en: [
      "Keep the heading levels and lists; on first use, keep proper nouns in the original language in brackets.",
      "Write for a general reader in plain, natural language; leave numbers and units in tables unchanged.",
    ],
  },
  ANALYZE_SPREADSHEET: {
    zh: [
      "按月份汇总销售额和订单数，找出增长最快的三个品类，并画一张趋势图。",
      "检查缺失值和重复行，清洗后给出每一列的基本统计，并说明发现的异常数据。",
    ],
    en: [
      "Total sales and orders per month, find the three fastest-growing categories and draw a trend chart.",
      "Check for missing values and duplicate rows, clean them, give basic statistics per column and explain any anomalies.",
    ],
  },
  CODE_REVIEW: {
    zh: [
      "重点检查安全问题（注入、鉴权、敏感信息泄露）和错误处理，按严重程度列出并给出修改建议。",
      "评估代码结构和可读性，指出最值得重构的 3 处，并说明原因。",
    ],
    en: [
      "Focus on security (injection, auth, leaked secrets) and error handling; list findings by severity with suggested fixes.",
      "Assess structure and readability and point out the three places most worth refactoring, with reasons.",
    ],
  },
  CODE_FIX: {
    zh: [
      "调用 parse_date(\"2024-02-30\") 时没有报错，而是返回了一个错误的日期；应当抛出 ValueError。请修复并补一个测试。",
    ],
    en: [
      "parse_date(\"2024-02-30\") returns a wrong date instead of raising ValueError. Fix it and add a test.",
    ],
  },
  WRITE_TESTS: {
    zh: ["为字符串处理函数补充单元测试，覆盖空字符串、超长输入和非 ASCII 字符。"],
    en: ["Add unit tests for the string helpers covering empty strings, very long input and non-ASCII characters."],
  },
  CODE_CHANGE: {
    zh: ["给命令行工具增加 --json 参数，以 JSON 格式输出结果，并更新 README 里的用法说明。"],
    en: ["Add a --json flag to the command-line tool that prints results as JSON, and update the usage section of the README."],
  },
};

// A task's name in lists and on its result page: the first line of what the member
// asked for, shortened. There is no separate title field.
export function taskTitle(task, max = 40) {
  const line = (task.requirement || task.prompt || "").trim().split(/\n/)[0].trim();
  const first = line.split(/(?<=[。！？!?])\s*|(?<=[.])\s+/)[0] || line;
  const chars = Array.from(first);
  return chars.length > max ? chars.slice(0, max - 1).join("") + "…" : first;
}

// The status chip: a translation key under "chip." and a tone for its colour.
export function statusChip(task) {
  const s = task.status;
  if (s === "SUCCEEDED") return { key: "done", tone: "ok" };
  if (s === "FAILED") return { key: "failed", tone: "bad" };
  if (s === "TIMED_OUT") return { key: "timed_out", tone: "bad" };
  if (s === "CANCELLED") return { key: "cancelled", tone: "idle" };
  if (s === "CANCELLING") return { key: "cancelling", tone: "run" };
  if (s === "VERIFYING") return { key: "verifying", tone: "run" };
  if (s === "RESERVED" || s === "RUNNING") return { key: "running", tone: "run" };
  if (task.approval_status === "PENDING") return { key: "approval", tone: "wait" };
  return { key: "queued", tone: "wait" };
}

const FINAL_EVENTS = new Set(["task.succeeded", "task.failed", "task.cancelled", "task.timed_out"]);

// Seconds the device has spent on the task: from its start to the final event, or
// to `now` while it runs. Null before it starts.
export function taskSeconds(task, events, now) {
  if (!task.started_at) return null;
  const end = [...events].reverse().find((e) => FINAL_EVENTS.has(e.type) && e.created_at);
  const stop = end ? Date.parse(end.created_at) : now;
  return Math.max(0, Math.round((stop - Date.parse(task.started_at)) / 1000));
}

// "Submitted → queued → started → done" from the event stream, each step once.
export function taskSteps(events) {
  const steps = [];
  for (const e of events) {
    const step = e.type.replace(/^task\./, "");
    if (step !== "progress" && step !== "cancelling" && steps[steps.length - 1] !== step)
      steps.push(step);
  }
  return steps;
}

const TEXT_PREVIEW = new Set(["md", "txt", "csv", "json", "html", "py", "js", "ts", "yaml", "yml"]);
const IMAGE_PREVIEW = new Set(["png", "jpg", "jpeg", "gif", "webp"]);

// Which result files get a preview on the result page.
export function previewKind(name) {
  const ext = (name || "").includes(".") ? name.split(".").pop().toLowerCase() : "";
  if (TEXT_PREVIEW.has(ext)) return "text";
  if (IMAGE_PREVIEW.has(ext)) return "image";
  return null;
}
