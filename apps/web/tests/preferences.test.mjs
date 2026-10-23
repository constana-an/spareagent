import test from "node:test";
import assert from "node:assert/strict";
import { limitsUpdate } from "../lib/preferences.mjs";

test("configure form sends only the limits it edits", () => {
  const form = new FormData();
  form.set("daily", "7");
  form.set("duration", "600");
  assert.deepEqual(limitsUpdate("conn-1", "group-1", form), {
    connection_id: "conn-1",
    group_id: "group-1",
    max_tasks_per_day: 7,
    max_task_duration_seconds: 600,
  });
});

test("empty limits mean unlimited instead of a zero allowance", () => {
  const form = new FormData();
  form.set("daily", "");
  form.set("duration", "");
  assert.deepEqual(limitsUpdate("conn-1", "group-1", form), {
    connection_id: "conn-1", group_id: "group-1",
    max_tasks_per_day: null, max_task_duration_seconds: null,
  });
});
