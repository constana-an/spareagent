// A narrow same-origin relay. Browser-supplied identity and upstream hosts never pass.
const uuid = "[0-9a-fA-F-]{36}";
const routes = [
  ["GET", `^friends/groups/${uuid}/audit$`],
  ["GET", "^friends/me$"],
  ["POST", "^friends/(login|logout)$"],
  ["GET", "^experiments/groups$"],
  ["GET", `^experiments/groups/${uuid}(/audit|/metrics)?$`],
  ["POST", `^experiments/groups/${uuid}/join$`],
  ["GET", "^subscriptions/(status|usage)$"],
  ["POST", "^subscriptions/(connect|attest|enrollment-tokens)$"],
  ["PUT", "^sharing/preferences$"],
  ["POST", "^sharing/(enable|pause|revoke)$"],
  ["GET", "^experimental-tasks(/nodes)?$"],
  ["POST", "^experimental-tasks$"],
  ["GET", `^experimental-tasks/${uuid}(/pilot-report)?$`],
  ["POST", `^experimental-tasks/${uuid}/(approve|reject)$`],
  ["GET", `^tasks/${uuid}/(events|artifacts)$`],
  ["GET", `^tasks/${uuid}/artifacts/${uuid}/content$`],
  ["POST", `^tasks/${uuid}/cancel$`],
];
export function allowed(method, path) {
  return routes.some(([m, p]) => method === m && new RegExp(p).test(path));
}
export async function relay(request, path, env = process.env, fetcher = fetch) {
  if (!allowed(request.method, path))
    return new Response("Not found", { status: 404 });
  const origin = env.FRIENDS_WEB_ORIGIN || "http://localhost:3000";
  const base = new URL(env.SPAREAGENT_API_URL || "http://127.0.0.1:8000");
  if (
    base.username ||
    base.password ||
    base.pathname !== "/" ||
    base.search ||
    base.hash ||
    (base.protocol !== "https:" &&
      !(
        base.protocol === "http:" &&
        ["localhost", "127.0.0.1", "[::1]"].includes(base.hostname)
      ))
  ) {
    return new Response("Invalid upstream configuration", { status: 503 });
  }
  if (request.method !== "GET" && request.headers.get("origin") !== origin) {
    return new Response("Origin rejected", { status: 403 });
  }
  const headers = new Headers();
  const cookie = (request.headers.get("cookie") || "")
    .split(";")
    .map((x) => x.trim())
    .find((x) => /^sa_friends_session=[A-Za-z0-9_-]{20,256}$/.test(x));
  if (cookie) headers.set("cookie", cookie);
  for (const name of ["content-type", "last-event-id", "idempotency-key"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (request.method !== "GET") headers.set("origin", origin);
  const target = new URL(`/v1/${path}`, base);
  const input = new URL(request.url);
  for (const name of ["group_id", "role", "limit"]) {
    const value = input.searchParams.get(name);
    if (value) target.searchParams.set(name, value);
  }
  try {
    const body = request.method === "GET" ? undefined : await request.text();
    if (body && body.length > 65536)
      return new Response("Request too large", { status: 413 });
    const upstream = await fetcher(target, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      redirect: "error",
      signal: request.signal,
    });
    const out = new Headers({
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
    for (const name of [
      "content-type",
      "set-cookie",
      "content-disposition",
      "x-artifact-sha256",
      "x-accel-buffering",
    ]) {
      const value = upstream.headers.get(name);
      if (value) out.set(name, value);
    }
    return new Response(upstream.body, {
      status: upstream.status,
      headers: out,
    });
  } catch {
    return new Response("API unavailable; reconnect shortly", { status: 502 });
  }
}
