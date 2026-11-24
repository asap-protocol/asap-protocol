import { decodeJwt } from "jose";
import { describe, expect, it } from "vitest";

import { createDemoIdentity } from "../src/demo-identity.js";

// verifyAgentJwt leaves the host binding to the resolver; the demo ignored it before.
describe("the demo agent resolver", () => {
  it("marks the agent usable only under the host that it belongs to", async () => {
    const demo = await createDemoIdentity();
    const hostThumbprint = decodeJwt(await demo.mintAgentJwt("https://mcp.example.com/mcp")).iss!;

    expect(await demo.resolveAgent({ agentId: demo.agentId, hostThumbprint })).toMatchObject({ usable: true });
    expect(await demo.resolveAgent({ agentId: demo.agentId, hostThumbprint: "another-host-thumbprint" })).toMatchObject({ usable: false });
    expect(await demo.resolveAgent({ agentId: "another-agent", hostThumbprint })).toBeUndefined();
  });
});
