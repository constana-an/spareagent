import test from "node:test";
import assert from "node:assert/strict";
import { allowed, relay } from "../lib/proxy.mjs";
const env = {
  FRIENDS_WEB_ORIGIN: "https://friends.example",
  SPAREAGENT_API_URL: "https://api.example",
};
test("relay refuses arbitrary URLs, admin routes, traversal and wrong methods", () => {
  for (const path of [
    "https://evil.example",
    "../friends/me",
    "workers/register",
    "friends/invites",
    "experiments/kill-switch",
  ])
    assert.equal(allowed("POST", path), false);
  assert.equal(allowed("POST", "friends/login"), true);
  assert.equal(allowed("GET", "friends/login"), false);
});
test("browser writes require configured origin", async () => {
  const r = await relay(
    new Request("https://friends.example/api/friends/login", {
      method: "POST",
      headers: { origin: "https://evil.example" },
    }),
    "friends/login",
    env,
    () => assert.fail("must not contact API"),
  );
  assert.equal(r.status, 403);
});
test("relay strips development identities, bearer credentials and foreign cookies", async () => {
  const r = await relay(
    new Request("https://friends.example/api/friends/me?evil=1", {
      headers: {
        "x-dev-user-id": "admin",
        authorization: "Bearer secret",
        cookie:
          "other=secret; sa_friends_session=abcdefghijklmnopqrstuvwxyz0123456789",
      },
    }),
    "friends/me",
    env,
    async (url, opts) => {
      assert.equal(url.href, "https://api.example/v1/friends/me");
      assert.equal(opts.headers.has("authorization"), false);
      assert.equal(opts.headers.has("x-dev-user-id"), false);
      assert.equal(
        opts.headers.get("cookie"),
        "sa_friends_session=abcdefghijklmnopqrstuvwxyz0123456789",
      );
      assert.equal(opts.redirect, "error");
      return Response.json({ role: "CONSUMER" });
    },
  );
  assert.equal(r.headers.get("cache-control"), "no-store");
});
test("SSE resume cursor and streaming body pass without buffering", async () => {
  const body = new ReadableStream({
    start(c) {
      c.enqueue(
        new TextEncoder().encode("id: 9\nevent: task.progress\ndata: {}\n\n"),
      );
      c.close();
    },
  });
  const r = await relay(
    new Request("https://friends.example/api/tasks/x/events", {
      headers: { "Last-Event-ID": "8" },
    }),
    "tasks/aaaaaaaa-0000-4000-8000-00000000000a/events",
    env,
    async (_, opts) => {
      assert.equal(opts.headers.get("last-event-id"), "8");
      return new Response(body, {
        headers: { "content-type": "text/event-stream" },
      });
    },
  );
  assert.equal(r.headers.get("content-type"), "text/event-stream");
  assert.match(await r.text(), /id: 9/);
});
test("invalid upstream configuration cannot become an open proxy", async () => {
  const r = await relay(
    new Request("https://friends.example/api/friends/me"),
    "friends/me",
    { ...env, SPAREAGENT_API_URL: "http://169.254.169.254" },
    () => assert.fail(),
  );
  assert.equal(r.status, 503);
});
test("login preserves secure cookie; artifact integrity header is forwarded", async () => {
  const r = await relay(
    new Request("https://friends.example/api/friends/login", {
      method: "POST",
      headers: {
        origin: env.FRIENDS_WEB_ORIGIN,
        "content-type": "application/json",
      },
      body: JSON.stringify({ token: "private-invite" }),
    }),
    "friends/login",
    env,
    async (_, opts) => {
      assert.equal(opts.headers.get("origin"), env.FRIENDS_WEB_ORIGIN);
      return new Response("{}", {
        headers: {
          "Set-Cookie":
            "sa_friends_session=opaque; Secure; HttpOnly; SameSite=Strict",
          "X-Artifact-SHA256": "abc",
        },
      });
    },
  );
  assert.match(r.headers.get("set-cookie"), /HttpOnly/);
  assert.equal(r.headers.get("x-artifact-sha256"), "abc");
});

test("pilot relay allows only read-only metrics and participant evidence; node diagnostics stay private", () => {
  const id = "aaaaaaaa-0000-4000-8000-00000000000a";
  for (const path of [
    `experiments/groups/${id}/metrics`,
    `experimental-tasks/${id}/pilot-report`,
  ]) {
    assert.equal(allowed("GET", path), true);
    assert.equal(allowed("POST", path), false);
  }
  assert.equal(allowed("GET", "workers/me/pilot-readiness"), false);
});

test("uploads relay raw bytes with an encoded file name only", async () => {
  let seen;
  const bytes = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0xff]);
  const r = await relay(
    new Request("https://friends.example/api/experimental-uploads", {
      method: "POST",
      headers: {
        origin: "https://friends.example",
        "x-filename": encodeURIComponent("报告.docx"),
        "content-type": "text/html",
      },
      body: bytes,
    }),
    "experimental-uploads",
    env,
    async (url, init) => {
      seen = { url: String(url), init };
      return new Response("{}", { status: 201 });
    },
  );
  assert.equal(r.status, 201);
  assert.equal(seen.url, "https://api.example/v1/experimental-uploads");
  assert.deepEqual(Array.from(seen.init.body), Array.from(bytes));
  assert.equal(seen.init.headers.get("content-type"), "application/octet-stream");
  assert.equal(seen.init.headers.get("x-filename"), encodeURIComponent("报告.docx"));
  const bad = await relay(
    new Request("https://friends.example/api/experimental-uploads", {
      method: "POST",
      headers: { origin: "https://friends.example", "x-filename": "a\"b" },
      body: bytes,
    }),
    "experimental-uploads",
    env,
    () => assert.fail("must not contact API"),
  );
  assert.equal(bad.status, 422);
  assert.equal(allowed("GET", "task-kinds"), true);
  assert.equal(allowed("PUT", "task-kinds/TRANSLATE_DOCUMENT/status"), false);
});

test("JSON bodies stay small; uploads allow 20 MB", async () => {
  const big = new Request("https://friends.example/api/experimental-tasks", {
    method: "POST",
    headers: { origin: "https://friends.example", "content-length": "70000" },
    body: "x".repeat(70000),
  });
  const r = await relay(big, "experimental-tasks", env, () => assert.fail("no"));
  assert.equal(r.status, 413);
});
test("the dashboard is accepted on each configured network origin, nothing else", async () => {
  const multi = {
    ...env,
    FRIENDS_WEB_EXTRA_ORIGINS: "https://100.64.0.9:13443, https://10.0.0.5:13443",
  };
  const send = (origin) =>
    relay(
      new Request("https://x/api/friends/login", {
        method: "POST",
        headers: { origin, "content-type": "application/json" },
        body: "{}",
      }),
      "friends/login",
      multi,
      async (_, opts) => {
        assert.equal(opts.headers.get("origin"), origin);
        return new Response("{}");
      },
    );
  for (const ok of ["https://friends.example", "https://100.64.0.9:13443", "https://10.0.0.5:13443"])
    assert.equal((await send(ok)).status, 200);
  assert.equal((await send("https://evil.example")).status, 403);
});

test("conversation history is a read-only dashboard route", () => {
  assert.equal(allowed("GET", "experimental-tasks/11111111-1111-4111-8111-111111111111/conversation"), true);
  assert.equal(allowed("POST", "experimental-tasks/11111111-1111-4111-8111-111111111111/conversation"), false);
});
