import { describe, expect, it } from "vitest";

import { lastAssistantText, sourceForSession } from "./index.js";

const config = {
  imessageSession: "agent:main:imessage:direct:owner@example.com",
  reachySession: "agent:main:reachy",
  statePath: "/tmp/test.json",
  summaryModel: "openai/gpt-5.4-mini",
};

describe("session binding", () => {
  it("maps only the two exact authenticated sessions", () => {
    expect(sourceForSession(config, config.imessageSession)).toBe("imessage");
    expect(sourceForSession(config, config.reachySession)).toBe("reachy");
    expect(sourceForSession(config, "agent:main:imessage:direct:someone-else")).toBeNull();
    expect(sourceForSession(config, "agent:main:reachy:lookalike")).toBeNull();
  });

  it("extracts only assistant text and ignores tool payloads", () => {
    expect(lastAssistantText([
      { role: "user", content: "hello" },
      { role: "assistant", content: [{ type: "toolCall", arguments: { secret: true } }, { type: "text", text: "Finished safely." }] },
    ])).toBe("Finished safely.");
  });
});
