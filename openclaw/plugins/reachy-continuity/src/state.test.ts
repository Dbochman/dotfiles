import { chmod, mkdir, readFile, stat, symlink, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { mkdtemp } from "node:fs/promises";

import { describe, expect, it } from "vitest";

import { CapsuleError, CapsuleStore, cleanSummary } from "./state.js";

async function fixture(now = 1_800_000_000_000) {
  const root = await mkdtemp(join(tmpdir(), "reachy-continuity-"));
  return { root, path: join(root, "private", "capsule.json"), store: new CapsuleStore(join(root, "private", "capsule.json"), () => now) };
}

describe("CapsuleStore", () => {
  it("returns only the other source's entries", async () => {
    const { store } = await fixture();
    await store.append("reachy", "Discussed camera orientation.");
    expect((await store.readFor("imessage")).entries).toHaveLength(1);
    expect((await store.readFor("reachy")).entries).toHaveLength(0);
  });

  it("returns the full capsule to the direct Reachy voice runtime", async () => {
    const { store } = await fixture();
    await store.append("reachy", "Reachy-side context.");
    await store.append("imessage", "iMessage-side context.");
    const view = await store.readAllFor("reachy");
    expect(view.entries.map((entry) => entry.source)).toEqual(["reachy", "imessage"]);
  });

  it("persists expiration pruning performed by direct-voice reads", async () => {
    let now = 1_800_000_000_000;
    const root = await mkdtemp(join(tmpdir(), "reachy-continuity-"));
    const path = join(root, "capsule.json");
    const store = new CapsuleStore(path, () => now);
    await store.append("imessage", "Temporary direct-voice context.");
    now += 4 * 60 * 60 * 1000 + 1;
    expect((await store.readAllFor("reachy")).entries).toHaveLength(0);
    const persisted = JSON.parse(await readFile(path, "utf8")) as { entries: unknown[] };
    expect(persisted.entries).toHaveLength(0);
  });

  it("caps entries at twelve", async () => {
    const { store } = await fixture();
    for (let index = 0; index < 15; index += 1) await store.append("imessage", `Turn ${index}`);
    const entries = (await store.readFor("reachy")).entries;
    expect(entries).toHaveLength(12);
    expect(entries[0].summary).toBe("Turn 3");
  });

  it("expires entries after four hours and handoffs after 24 hours", async () => {
    let now = 1_800_000_000_000;
    const root = await mkdtemp(join(tmpdir(), "reachy-continuity-"));
    const store = new CapsuleStore(join(root, "capsule.json"), () => now);
    await store.append("reachy", "Short-lived summary.");
    await store.createHandoff("reachy", "Longer-lived handoff.");
    now += 4 * 60 * 60 * 1000 + 1;
    expect((await store.readFor("imessage")).entries).toHaveLength(0);
    expect((await store.readFor("imessage")).handoffs).toHaveLength(1);
    now += 20 * 60 * 60 * 1000;
    expect((await store.readFor("imessage")).handoffs).toHaveLength(0);
  });

  it("consumes only the selected targeted handoff", async () => {
    const { store } = await fixture();
    const first = await store.createHandoff("reachy", "First handoff.");
    await store.createHandoff("reachy", "Second handoff.");
    await store.consume("imessage", first.id);
    const handoffs = (await store.readFor("imessage")).handoffs;
    expect(handoffs).toHaveLength(1);
    await expect(store.consume("reachy", handoffs[0].id)).rejects.toBeInstanceOf(CapsuleError);
  });

  it("deduplicates asynchronous summaries by run id", async () => {
    const { store } = await fixture();
    await store.append("reachy", "One summary.", "run-1");
    await store.append("reachy", "Duplicate summary.", "run-1");
    expect((await store.readFor("imessage")).entries).toHaveLength(1);
  });

  it("uses owner-only directory and file modes", async () => {
    const { path, store } = await fixture();
    await store.append("reachy", "Permission check.");
    expect((await stat(join(path, ".."))).mode & 0o777).toBe(0o700);
    expect((await stat(path)).mode & 0o777).toBe(0o600);
  });

  it("fails closed without overwriting malformed state", async () => {
    const { path, store } = await fixture();
    await mkdir(join(path, ".."), { recursive: true });
    await writeFile(path, "{not-json", { mode: 0o600 });
    await expect(store.readFor("reachy")).rejects.toBeInstanceOf(CapsuleError);
    expect(await readFile(path, "utf8")).toBe("{not-json");
  });

  it("rejects symlink state paths", async () => {
    const { root } = await fixture();
    const target = join(root, "target.json");
    const path = join(root, "linked.json");
    await writeFile(target, "{}", { mode: 0o600 });
    await symlink(target, path);
    await expect(new CapsuleStore(path).readFor("reachy")).rejects.toBeInstanceOf(CapsuleError);
  });

  it("rejects secret-like summaries and oversized summaries", () => {
    expect(() => cleanSummary("API key: sk-example1234567890")).toThrow(CapsuleError);
    expect(() => cleanSummary("x".repeat(1201))).toThrow(CapsuleError);
  });

  it("repairs restrictive modes on an existing state directory", async () => {
    const { path, store } = await fixture();
    await mkdir(join(path, ".."), { recursive: true, mode: 0o755 });
    await chmod(join(path, ".."), 0o755);
    await store.append("reachy", "Mode repair.");
    expect((await stat(join(path, ".."))).mode & 0o777).toBe(0o700);
  });
});
