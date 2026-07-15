import { describe, expect, it } from "vitest";

import { buildDirectVoiceContext, lastAssistantText, sourceForSession } from "./index.js";

const config = {
  imessageSession: "agent:main:imessage:direct:owner@example.com",
  reachySession: "agent:main:reachy",
  statePath: "/tmp/test.json",
  summaryModel: "openai/gpt-5.4-mini",
  identityPath: "/tmp/IDENTITY.md",
  soulPath: "/tmp/SOUL.md",
  userPath: "/tmp/USER.md",
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

  it("builds a stable direct-voice snapshot from identity, SOUL, user, and capsule", () => {
    const view = {
      updatedAt: 123,
      entries: [{ id: "1", ts: 123, source: "imessage" as const, summary: "Discussed dinner." }],
      handoffs: [],
    };
    const first = buildDirectVoiceContext(
      "  My name is Claude.  ",
      "  Be useful.  ",
      "  Dylan is the owner.  ",
      view,
    );
    const second = buildDirectVoiceContext(
      "My name is Claude.",
      "Be useful.",
      "Dylan is the owner.",
      { ...view, updatedAt: 456 },
    );
    expect(first.identity).toBe("My name is Claude.");
    expect(first.soul).toBe("Be useful.");
    expect(first.user).toBe("Dylan is the owner.");
    expect(first.capsule).toContain("Discussed dinner.");
    expect(first.revision).toBe(second.revision);
  });
});
