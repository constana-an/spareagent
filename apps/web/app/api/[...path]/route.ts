import { relay } from "../../../lib/proxy.mjs";
export const dynamic = "force-dynamic";
export const runtime = "nodejs";
async function handle(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
) {
  return relay(request, (await context.params).path.join("/"));
}
export { handle as GET, handle as POST, handle as PUT };
