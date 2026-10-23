import test from "node:test";
import assert from "node:assert/strict";
import {
  draftFrom,
  EXAMPLES,
  MAX_UPLOAD,
  mergeAttachments,
  newlyFinished,
  pastedName,
  previewKind,
  statusChip,
  taskSeconds,
  taskSteps,
  taskTitle,
} from "../lib/task-form.mjs";

const general = { extensions: ["md", "png", "csv"], max_attachments: 2 };
const file = (name, size = 10) => ({ name, size });

test("SSE timestamp fixes completed duration and missing history never keeps a finished clock running", () => {
  const task = { status: "SUCCEEDED", started_at: "2026-10-22T20:46:00Z" };
  const events = [{ type: "task.succeeded", timestamp: "2026-10-22T20:46:12Z" }];
  assert.equal(taskSeconds(task, events, Date.parse("2026-10-22T21:00:00Z")), 12);
  assert.equal(taskSeconds(task, events, Date.parse("2026-10-23T21:00:00Z")), 12);
  assert.equal(taskSeconds(task, [], Date.now()), null);
  assert.equal(taskSeconds({ ...task, status: "RUNNING" }, [], Date.parse("2026-10-22T20:46:30Z")), 30);
  assert.equal(taskSeconds({ ...task, started_at: null }, events, Date.now()), null);
  assert.equal(taskSeconds(task, [{ type: "task.succeeded", timestamp: "bad" }], Date.now()), null);
});

test("attachments keep allowed types within the size and count limits", () => {
  const { files, rejected } = mergeAttachments(
    [file("a.md")],
    [file("b.PNG"), file("c.exe"), file("d.csv", MAX_UPLOAD + 1), file("e.csv"), file("noext")],
    general,
  );
  assert.deepEqual(files.map((f) => f.name), ["a.md", "b.PNG"]);
  assert.deepEqual(rejected, [
    { name: "c.exe", reason: "type" },
    { name: "d.csv", reason: "size" },
    { name: "e.csv", reason: "count" },
    { name: "noext", reason: "type" },
  ]);
});

test("the same file dropped twice is kept once", () => {
  const { files, rejected } = mergeAttachments([file("a.md")], [file("a.md")], general);
  assert.equal(files.length, 1);
  assert.deepEqual(rejected, []);
});

test("pasted images get distinct, dated names", () => {
  const now = new Date(2026, 9, 22, 9, 5, 7);
  assert.equal(pastedName("image/png", now), "pasted-20261022-090507.png");
  assert.equal(pastedName("image/jpeg", now, 1), "pasted-20261022-090507-2.jpg");
});

test("only transitions to a final state are announced, never on the first poll", () => {
  const first = newlyFinished(null, [{ id: "1", status: "SUCCEEDED" }]);
  assert.deepEqual(first.finished, []);
  const next = newlyFinished(
    new Map([
      ["1", "RUNNING"],
      ["2", "VERIFYING"],
      ["3", "RUNNING"],
      ["4", "FAILED"],
    ]),
    [
      { id: "1", status: "SUCCEEDED" },
      { id: "2", status: "VERIFYING" },
      { id: "3", status: "CANCELLED" },
      { id: "4", status: "FAILED" },
      { id: "5", status: "FAILED" },
    ],
  );
  assert.deepEqual(next.finished.map((t) => t.id), ["1"]);
  assert.equal(next.seen.get("5"), "FAILED");
});

test("submit again restores the request and lists files to re-add", () => {
  const d = draftFrom({
    kind: "TRANSLATE_DOCUMENT",
    task_type: "TRANSLATE_DOCUMENT",
    requirement: "Keep headings",
    prompt: "internal prompt",
    repository_url: null,
    commit_sha: null,
    parameters: { target_language: "ja" },
    deliverable: { format: "md", filename: "translation.md" },
    network_access: true,
    max_duration_seconds: 300,
    execution_mode: "LIVE",
    connection_id: "c-1",
    attachments: [{ id: "x", filename: "doc.docx", size_bytes: 5 }],
  });
  assert.deepEqual(d, {
    kind: "TRANSLATE_DOCUMENT",
    prompt: "Keep headings",
    repo: "",
    sha: "",
    target_language: "ja",
    format: "md",
    network: true,
    timeout: 300,
    mode: "LIVE",
    connection: "c-1",
    attachments: ["doc.docx"],
  });
});

test("every example kind has the same number of examples in both languages", () => {
  for (const [kind, ex] of Object.entries(EXAMPLES))
    assert.equal(ex.zh.length, ex.en.length, kind);
});

test("a task is named by the first sentence of its request", () => {
  assert.equal(taskTitle({ requirement: "写一封邮件。语气礼貌。" }), "写一封邮件。");
  assert.equal(taskTitle({ requirement: "Fix the bug. Then add a test." }), "Fix the bug.");
  assert.equal(taskTitle({ prompt: "第一行\n第二行" }), "第一行");
  assert.equal(taskTitle({ requirement: "一".repeat(50) }, 10), "一".repeat(9) + "…");
  assert.equal(taskTitle({ requirement: "  " }), "");
});

test("status chips follow the task status and approval", () => {
  const chip = (status, approval_status = "APPROVED") =>
    statusChip({ status, approval_status });
  assert.deepEqual(chip("SUCCEEDED"), { key: "done", tone: "ok" });
  assert.deepEqual(chip("TIMED_OUT"), { key: "timed_out", tone: "bad" });
  assert.deepEqual(chip("RUNNING"), { key: "running", tone: "run" });
  assert.deepEqual(chip("QUEUED", "PENDING"), { key: "approval", tone: "wait" });
  assert.deepEqual(chip("QUEUED"), { key: "queued", tone: "wait" });
  assert.deepEqual(chip("CANCELLED"), { key: "cancelled", tone: "idle" });
});

test("run time counts from the start to the final event, or to now", () => {
  const task = { status: "RUNNING", started_at: "2026-10-22T10:00:00Z" };
  const events = [
    { type: "task.started", timestamp: "2026-10-22T10:00:00Z" },
    { type: "task.succeeded", timestamp: "2026-10-22T10:01:12Z" },
  ];
  assert.equal(taskSeconds(task, events, Date.parse("2026-10-22T11:00:00Z")), 72);
  assert.equal(taskSeconds(task, events.slice(0, 1), Date.parse("2026-10-22T10:00:30Z")), 30);
  assert.equal(taskSeconds({ started_at: null }, events, 0), null);
});

test("steps list each event kind once, without progress lines", () => {
  const ev = (type) => ({ type });
  assert.deepEqual(
    taskSteps(["created", "queued", "started", "progress", "progress", "verifying", "succeeded"]
      .map((x) => ev(`task.${x}`))),
    ["created", "queued", "started", "verifying", "succeeded"],
  );
});

test("text and image results get a preview, others do not", () => {
  assert.equal(previewKind("answer.md"), "text");
  assert.equal(previewKind("chart.PNG"), "image");
  assert.equal(previewKind("report.docx"), null);
  assert.equal(previewKind(null), null);
});
