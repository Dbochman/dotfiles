import { describe, expect, it } from "vitest";

import { buildDirectVoiceContext, lastAssistantText, sourceForSession } from "./index.js";

const config = {
  imessageSession: "agent:main:imessage:direct:owner@example.com",
  reachySession: "agent:main:reachy",
  statePath: "/tmp/test.json",
  summaryModel: "openai/gpt-5.4-mini",
  soulPath: "/tmp/SOUL.md",
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

  it("builds a stable direct-voice snapshot from SOUL and capsule summaries", () => {
    const view = {
      updatedAt: 123,
      entries: [{ id: "1", ts: 123, source: "imessage" as const, summary: "Discussed dinner." }],
      handoffs: [],
    };
    const first = buildDirectVoiceContext("  Be useful.  ", view);
    const second = buildDirectVoiceContext("Be useful.", { ...view, updatedAt: 456 });
    expect(first.soul).toBe("Be useful.");
    expect(first.capsule).toContain("Discussed dinner.");
    expect(first.revision).toBe(second.revision);
  });
});
