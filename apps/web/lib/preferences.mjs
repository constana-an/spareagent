// The web "configure" form edits only the daily and duration limits. The API
// keeps any field left out of PUT sharing/preferences, so sending just these
// two leaves concurrency, task kinds and approval settings from the Mac app alone.
export function limitsUpdate(connectionId, groupId, form) {
  return {
    connection_id: connectionId,
    group_id: groupId,
    max_tasks_per_day: Number(form.get("daily")),
    max_task_duration_seconds: Number(form.get("duration")),
  };
}
