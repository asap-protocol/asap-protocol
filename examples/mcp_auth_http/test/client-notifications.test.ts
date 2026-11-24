import { EventEmitter, once } from "node:events";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";

import { createAgent, createHost, MemoryStorage, type AgentContext } from "@asap-protocol/client";
import { createMcpAuthMiddleware, staticAgentGrants } from "@asap-protocol/mcp-auth";
import { Client, InsufficientScopeError, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { toNodeHandler } from "@modelcontextprotocol/node";
import { createMcpHandler, McpServer, type ServerContext } from "@modelcontextprotocol/server";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import * as z from "zod";

/**
 * Deny by default refuses every notification that
 * `allowMethods` does not list. This pins what the pinned SDK client sends on
 * its own in revision 2026-07-28, so the newest-2.x CI row fails if a later
 * SDK starts to send a notification that the token layer would refuse.
 */
const SLOW_TOOL = "slow";
const PROGRESS_TOOL = "progress";

interface SeenPost {
  method: unknown;
  isNotification: boolean;
  mcpMethodHeader: string | null;
  status: number;
}

type SlowToolEvents = EventEmitter<{ slowStarted: []; slowAborted: [] }>;

let agent: AgentContext;
let running: { server: Server; url: URL; posts: SeenPost[]; events: SlowToolEvents } | undefined;

beforeAll(async () => {
  agent = await createAgent(await createHost({ storage: new MemoryStorage() }), { mode: "delegated" });
});

afterEach(async () => {
  running?.server.closeAllConnections();
  await new Promise<void>((resolve) => running?.server.close(() => resolve()) ?? resolve());
  running = undefined;
});

function buildServer(auth: ReturnType<typeof createMcpAuthMiddleware>, events: SlowToolEvents): McpServer {
  const server = new McpServer({ name: "notification-probe", version: "0.0.0" });
  const slow = async (ctx: ServerContext) => {
    await new Promise<void>((resolve) => {
      ctx.mcpReq.signal.addEventListener("abort", () => {
        events.emit("slowAborted");
        resolve();
      }, { once: true });
      events.emit("slowStarted");
    });
    return { content: [{ type: "text" as const, text: "done" }] };
  };
  server.registerTool(SLOW_TOOL, {}, auth.protectTool(SLOW_TOOL, slow));
  server.registerTool(
    PROGRESS_TOOL,
    { inputSchema: z.object({}) },
    auth.protectTool(PROGRESS_TOOL, async (_args, ctx) => {
      const progressToken = ctx.mcpReq._meta?.progressToken;
      if (progressToken !== undefined) await ctx.mcpReq.notify({ method: "notifications/progress", params: { progressToken, progress: 1, total: 1 } });
      return { content: [{ type: "text", text: "progressed" }] };
    }),
  );
  return server;
}

/** A protected server on a free port that records every POST and its answer. */
async function start(allowMethods: string[] = []): Promise<NonNullable<typeof running>> {
  const posts: SeenPost[] = [];
  const events: SlowToolEvents = new EventEmitter();
  const server = createServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const url = new URL(`http://127.0.0.1:${(server.address() as AddressInfo).port}/mcp`);
  const auth = createMcpAuthMiddleware({
    agentJwt: { audience: url.href, resolveAgent: async ({ agentId }) => (agentId === agent.agentId ? { publicKey: agent.publicJwk, usable: true } : undefined) },
    toolCapabilities: { [SLOW_TOOL]: SLOW_TOOL, [PROGRESS_TOOL]: PROGRESS_TOOL },
    agentGrants: staticAgentGrants({ [agent.agentId]: [{ capability: SLOW_TOOL }, { capability: PROGRESS_TOOL }] }),
    allowMethods,
  });
  const app = auth.protect(createMcpHandler(() => buildServer(auth, events), { legacy: "reject" }));
  const recording = async (request: Request) => {
    const body = (await request.clone().json()) as { method?: unknown; id?: unknown };
    const response = await app.fetch(request);
    posts.push({ method: body.method, isNotification: body.id === undefined, mcpMethodHeader: request.headers.get("mcp-method"), status: response.status });
    return response;
  };
  server.on("request", toNodeHandler({ fetch: recording }));
  running = { server, url, posts, events };
  return running;
}

async function connect(url: URL, mode: "auto" | { pin: string }) {
  const errors: unknown[] = [];
  const transport = new StreamableHTTPClientTransport(url, {
    authProvider: { token: () => agent.signAgentJwt({ aud: url.href, capabilities: [SLOW_TOOL, PROGRESS_TOOL] }) },
  });
  const client = new Client({ name: "notification-probe", version: "0.0.0" }, { versionNegotiation: { mode } });
  client.onerror = (error) => errors.push(error);
  await client.connect(transport);
  return { client, errors };
}

async function expectAbortOnTimeout(client: Client, events: SlowToolEvents): Promise<void> {
  const timeout = 50;
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  try {
    const started = once(events, "slowStarted");
    const aborted = once(events, "slowAborted");
    const rejection = expect(client.callTool({ name: SLOW_TOOL }, { timeout })).rejects.toThrow("timed out");
    // Advance the SDK timer after the tool installs its abort listener.
    await started;
    vi.advanceTimersByTime(timeout);
    await rejection;
    await aborted;
  } finally {
    vi.useRealTimers();
  }
}

describe("what the SDK client sends on its own to a protected server (revision 2026-07-28)", () => {
  it.each<[string, "auto" | { pin: string }]>([
    ["pinned to 2026-07-28", { pin: "2026-07-28" }],
    ["in auto mode", "auto"],
  ])("%s: no notification on connect, call, progress, cancel, timeout, or close", async (_mode, mode) => {
    const { url, posts, events } = await start();
    const { client, errors } = await connect(url, mode);

    await client.listTools();
    const progress: unknown[] = [];
    await client.callTool({ name: PROGRESS_TOOL, arguments: {} }, { onprogress: (update) => progress.push(update) });
    const abort = new AbortController();
    const started = once(events, "slowStarted");
    const aborted = once(events, "slowAborted");
    const cancelled = expect(client.callTool({ name: SLOW_TOOL }, { signal: abort.signal })).rejects.toThrow("caller gave up");
    await started;
    abort.abort("caller gave up");
    await cancelled;
    await aborted;
    await expectAbortOnTimeout(client, events);
    await client.close();

    expect(progress).toHaveLength(1);
    await expect.poll(() => posts.map((post) => post.method)).toEqual(["server/discover", "tools/list", "tools/call", "tools/call", "tools/call"]);
    expect(posts.filter((post) => post.isNotification)).toEqual([]);
    expect(posts.filter((post) => post.status === 401 || post.status === 403)).toEqual([]);
    expect(errors).toEqual([]);
  });

  it("closing a subscriptions/listen subscription POSTs notifications/cancelled with no Mcp-Method; the 403 goes to onerror and the client goes on", async () => {
    // allowMethods opens subscriptions/listen; it cannot open the notification, which carries no Mcp-Method header.
    const { url, posts } = await start(["subscriptions/listen", "notifications/cancelled"]);
    const { client, errors } = await connect(url, { pin: "2026-07-28" });

    const subscription = await client.listen({ toolsListChanged: true });
    await subscription.close();
    await expect.poll(() => errors.length).toBe(1);
    const after = await client.callTool({ name: PROGRESS_TOOL, arguments: {} });
    await client.close();

    expect(posts.find((post) => post.isNotification)).toEqual({ method: "notifications/cancelled", isNotification: true, mcpMethodHeader: null, status: 403 });
    expect(errors[0]).toBeInstanceOf(InsufficientScopeError);
    expect(after.content).toEqual([{ type: "text", text: "progressed" }]);
  });
});
