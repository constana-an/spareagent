import test from "node:test";
import assert from "node:assert/strict";
import {
  draftFrom,
  EXAMPLES,
  MAX_UPLOAD,
  mergeAttachments,
  newlyFinished,
  pastedName,
} from "../lib/task-form.mjs";

const general = { extensions: ["md", "png", "csv"], max_attachments: 2 };
const file = (name, size = 10) => ({ name, size });

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
