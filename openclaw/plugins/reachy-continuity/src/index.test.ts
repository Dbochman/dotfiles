import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it, vi } from "vitest";

import plugin, {
  buildDirectVoiceContext,
  lastAssistantText,
  remembersExplicitly,
  sourceForSession,
} from "./index.js";

const config = {
  imessageSession: "agent:main:imessage:direct:owner@example.com",
  imessageTarget: "owner@example.com",
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

  it.each([
    "Remember my preference for cedar.",
    "Please save my preference for later.",
    "Can you remember this preference?",
    "I want you to write down my preference.",
    "Make sure you remember my preferred voice.",
  ])("authorizes an explicit memory-write request: %s", (prompt) => {
    expect(remembersExplicitly(prompt)).toBe(true);
  });

  it.each([
    "Do you remember that preference?",
    "What do you remember about me?",
    "I remember that preference.",
    "Don't remember this.",
    "Please do not save that.",
    "Can you not remember this?",
    "I don't want you to remember this.",
    "Remember?",
  ])("does not authorize questions, statements, negations, or empty requests: %s", (prompt) => {
    expect(remembersExplicitly(prompt)).toBe(false);
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

  it("summarizes the verified delivered iMessage rather than assistant tool payloads", async () => {
    const root = await mkdtemp(join(tmpdir(), "reachy-continuity-plugin-"));
    const statePath = join(root, "capsule.json");
    const hooks = new Map<string, (...args: any[]) => unknown>();
    const complete = vi.fn().mockResolvedValue({ text: '{"summary":"Reviewed the request and delivered the final outcome."}' });
    const api = {
      pluginConfig: { ...config, statePath },
      logger: { warn: vi.fn() },
      runtime: { llm: { complete } },
      on: vi.fn((name: string, handler: (...args: any[]) => unknown) => hooks.set(name, handler)),
      registerGatewayMethod: vi.fn(),
      registerTool: vi.fn(),
    };
    (plugin as unknown as { register(api: unknown): void }).register(api);

    const context = {
      sessionKey: config.imessageSession,
      runId: "run-imessage-delivery",
      agentId: "main",
    };
    await hooks.get("before_prompt_build")?.({ prompt: "Please review the change." }, context);
    await hooks.get("message_sent")?.({
      to: config.imessageTarget,
      content: "The verified delivered outcome.",
      success: true,
      sessionKey: config.imessageSession,
    }, { sessionKey: config.imessageSession });
    await hooks.get("agent_end")?.({
      success: true,
      runId: context.runId,
      messages: [{ role: "assistant", content: [{ type: "toolCall", arguments: { internal: "payload" } }] }],
    }, context);

    await vi.waitFor(() => expect(complete).toHaveBeenCalledOnce());
    const request = complete.mock.calls[0][0] as { messages: Array<{ content: string }> };
    expect(request.messages[0].content).toContain("The verified delivered outcome.");
    expect(request.messages[0].content).not.toContain("internal");
    await vi.waitFor(async () => {
      const state = JSON.parse(await readFile(statePath, "utf8")) as { entries: unknown[] };
      expect(state.entries).toHaveLength(1);
    });
  });
});
