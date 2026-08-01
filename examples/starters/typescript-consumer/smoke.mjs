#!/usr/bin/env node
/**
 * Thin TypeScript consumer smoke.
 *
 * Offline by default: createHost + createAgent with MemoryStorage.
 * Optional live path: set ASAP_PROVIDER_URL to discover + list capabilities.
 *
 * Non-loopback URLs must use HTTPS (TLS). No API keys required for the default path.
 */
import {
  createHost,
  createAgent,
  MemoryStorage,
  discoverProvider,
  listCapabilities,
} from "@asap-protocol/client";
import {
  assertHttpsOrLoopback,
  redactSecretsForLog,
} from "./smoke_helpers.mjs";

/** Live-path bound matching Python starter subprocess timeouts. */
const LIVE_TIMEOUT_MS = 60_000;

async function runOfflineIdentity() {
  const storage = new MemoryStorage();
  const host = await createHost({ storage });
  const agent = await createAgent(host, { mode: "delegated" });
  return { host, agent };
}

async function runOptionalLive(agent) {
  const raw = process.env.ASAP_PROVIDER_URL?.trim();
  if (raw === undefined || raw.length === 0) {
    return null;
  }

  assertHttpsOrLoopback(raw, "ASAP_PROVIDER_URL");

  const signal = AbortSignal.timeout(LIVE_TIMEOUT_MS);
  const manifest = await discoverProvider(raw, { signal });
  const asapEndpoint = manifest.endpoints?.asap;
  if (typeof asapEndpoint !== "string" || asapEndpoint.length === 0) {
    throw new Error(
      `manifest.endpoints.asap must be a non-empty string URL, got: ${JSON.stringify(asapEndpoint)}`,
    );
  }
  const provider = assertHttpsOrLoopback(
    asapEndpoint,
    "manifest.endpoints.asap",
  );
  const agentJwt = await agent.signAgentJwt({ aud: provider.origin });
  const listed = await listCapabilities(provider, { agentJwt, signal });
  return {
    providerId: manifest.id,
    providerName: manifest.name,
    capabilityCount: listed.capabilities.length,
  };
}

async function main() {
  const { host, agent } = await runOfflineIdentity();
  console.log(
    `identity ok: host=${host.hostId} agent=${agent.agentId} mode=${agent.mode}`,
  );

  const live = await runOptionalLive(agent);
  if (live !== null) {
    console.log(
      `live ok: provider=${live.providerId} (${live.providerName}) capabilities=${live.capabilityCount}`,
    );
  } else {
    console.log("live skipped: ASAP_PROVIDER_URL unset (offline smoke)");
  }

  console.log("typescript-consumer smoke: PASS");
}

main().catch((err) => {
  const raw = err instanceof Error ? err.message : String(err);
  console.error(
    `typescript-consumer smoke: FAIL — ${redactSecretsForLog(raw)}`,
  );
  process.exit(1);
});
