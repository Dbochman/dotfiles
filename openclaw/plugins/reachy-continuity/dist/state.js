import { constants } from "node:fs";
import { chmod, lstat, mkdir, open, readFile, rename, rm, stat } from "node:fs/promises";
import { dirname } from "node:path";
import { randomUUID } from "node:crypto";
const ENTRY_TTL_MS = 4 * 60 * 60 * 1000;
const HANDOFF_TTL_MS = 24 * 60 * 60 * 1000;
const MAX_ENTRIES = 12;
const MAX_HANDOFFS = 8;
const MAX_SUMMARY_CHARS = 1200;
const LOCK_TIMEOUT_MS = 2500;
const STALE_LOCK_MS = 30_000;
const SOURCES = new Set(["imessage", "reachy"]);
const SECRET_PATTERNS = [
    /\bsk-[A-Za-z0-9_-]{12,}\b/,
    /\b(?:password|passwd|api[_ -]?key|access[_ -]?token|bearer)\s*[:=]\s*\S+/i,
    /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
    /\b(?:\d[ -]*?){13,19}\b/,
];
export class CapsuleError extends Error {
}
function emptyState() {
    return { version: 2, updatedAt: 0, entries: [], handoffs: [] };
}
function isRecord(value) {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}
export function cleanSummary(value) {
    const text = value.replaceAll("\0", "").replace(/\s+/g, " ").trim();
    if (!text)
        throw new CapsuleError("summary must not be empty");
    if (text.length > MAX_SUMMARY_CHARS) {
        throw new CapsuleError(`summary exceeds ${MAX_SUMMARY_CHARS} characters`);
    }
    if (SECRET_PATTERNS.some((pattern) => pattern.test(text))) {
        throw new CapsuleError("summary contains secret-like or payment data");
    }
    return text;
}
function parseSource(value) {
    if (typeof value !== "string" || !SOURCES.has(value)) {
        throw new CapsuleError("capsule contains an invalid source");
    }
    return value;
}
function parseEntry(value) {
    if (!isRecord(value) || typeof value.id !== "string" || typeof value.ts !== "number") {
        throw new CapsuleError("capsule contains a malformed entry");
    }
    return {
        id: value.id,
        ts: value.ts,
        source: parseSource(value.source),
        summary: cleanSummary(String(value.summary ?? "")),
        ...(typeof value.runId === "string" ? { runId: value.runId } : {}),
    };
}
function parseHandoff(value) {
    if (!isRecord(value) || typeof value.id !== "string" || typeof value.ts !== "number") {
        throw new CapsuleError("capsule contains a malformed handoff");
    }
    const from = parseSource(value.from);
    const to = parseSource(value.to);
    if (from === to)
        throw new CapsuleError("handoff source and target must differ");
    return { id: value.id, ts: value.ts, from, to, summary: cleanSummary(String(value.summary ?? "")) };
}
function parseState(value) {
    if (!isRecord(value) || value.version !== 2 || !Array.isArray(value.entries) || !Array.isArray(value.handoffs)) {
        throw new CapsuleError("capsule has an unsupported or malformed version");
    }
    return {
        version: 2,
        updatedAt: typeof value.updatedAt === "number" ? value.updatedAt : 0,
        entries: value.entries.map(parseEntry),
        handoffs: value.handoffs.map(parseHandoff),
    };
}
function prune(state, now) {
    state.entries = state.entries
        .filter((item) => item.ts <= now && now - item.ts <= ENTRY_TTL_MS)
        .slice(-MAX_ENTRIES);
    state.handoffs = state.handoffs
        .filter((item) => item.ts <= now && now - item.ts <= HANDOFF_TTL_MS)
        .slice(-MAX_HANDOFFS);
}
async function ensurePrivateDirectory(path) {
    await mkdir(path, { recursive: true, mode: 0o700 });
    await chmod(path, 0o700);
}
async function rejectSymlink(path) {
    try {
        const info = await lstat(path);
        if (info.isSymbolicLink())
            throw new CapsuleError("capsule path must not be a symlink");
    }
    catch (error) {
        if (error.code !== "ENOENT")
            throw error;
    }
}
async function sleep(ms) {
    await new Promise((resolve) => setTimeout(resolve, ms));
}
export class CapsuleStore {
    path;
    now;
    constructor(path, now = Date.now) {
        this.path = path;
        this.now = now;
    }
    async acquireLock() {
        const lockPath = `${this.path}.lock`;
        const started = Date.now();
        await ensurePrivateDirectory(dirname(this.path));
        while (true) {
            try {
                await mkdir(lockPath, { mode: 0o700 });
                return async () => rm(lockPath, { recursive: true, force: true });
            }
            catch (error) {
                if (error.code !== "EEXIST")
                    throw error;
                try {
                    const info = await stat(lockPath);
                    if (Date.now() - info.mtimeMs > STALE_LOCK_MS) {
                        const stalePath = `${lockPath}.stale-${randomUUID()}`;
                        await rename(lockPath, stalePath);
                        await rm(stalePath, { recursive: true, force: true });
                        continue;
                    }
                }
                catch (statError) {
                    if (statError.code === "ENOENT")
                        continue;
                }
                if (Date.now() - started >= LOCK_TIMEOUT_MS)
                    throw new CapsuleError("capsule lock timed out");
                await sleep(15);
            }
        }
    }
    async withLock(operation) {
        const release = await this.acquireLock();
        try {
            return await operation();
        }
        finally {
            await release();
        }
    }
    async load() {
        await rejectSymlink(this.path);
        try {
            return parseState(JSON.parse(await readFile(this.path, "utf8")));
        }
        catch (error) {
            if (error.code === "ENOENT")
                return emptyState();
            if (error instanceof CapsuleError)
                throw error;
            throw new CapsuleError(`cannot read capsule safely: ${error.message}`);
        }
    }
    async save(state) {
        const parent = dirname(this.path);
        await ensurePrivateDirectory(parent);
        await rejectSymlink(this.path);
        state.updatedAt = this.now();
        const temporary = `${this.path}.tmp-${process.pid}-${randomUUID()}`;
        const handle = await open(temporary, constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY, 0o600);
        try {
            await handle.writeFile(`${JSON.stringify(state)}\n`, "utf8");
            await handle.sync();
        }
        finally {
            await handle.close();
        }
        try {
            await rename(temporary, this.path);
            await chmod(this.path, 0o600);
            const directory = await open(parent, constants.O_RDONLY);
            try {
                await directory.sync();
            }
            finally {
                await directory.close();
            }
        }
        catch (error) {
            await rm(temporary, { force: true });
            throw error;
        }
    }
    async readFor(source) {
        return this.withLock(async () => {
            const state = await this.load();
            prune(state, this.now());
            await this.save(state);
            return {
                entries: state.entries.filter((item) => item.source !== source),
                handoffs: state.handoffs.filter((item) => item.to === source),
            };
        });
    }
    async append(source, summary, runId) {
        const normalized = cleanSummary(summary);
        await this.withLock(async () => {
            const state = await this.load();
            prune(state, this.now());
            if (runId && state.entries.some((item) => item.runId === runId))
                return;
            state.entries.push({ id: randomUUID(), ts: this.now(), source, summary: normalized, ...(runId ? { runId } : {}) });
            prune(state, this.now());
            await this.save(state);
        });
    }
    async createHandoff(from, summary) {
        const normalized = cleanSummary(summary);
        const to = from === "reachy" ? "imessage" : "reachy";
        return this.withLock(async () => {
            const state = await this.load();
            prune(state, this.now());
            const handoff = { id: randomUUID(), ts: this.now(), from, to, summary: normalized };
            state.handoffs.push(handoff);
            prune(state, this.now());
            await this.save(state);
            return handoff;
        });
    }
    async consume(source, id) {
        await this.withLock(async () => {
            const state = await this.load();
            prune(state, this.now());
            const target = state.handoffs.find((item) => item.id === id);
            if (!target || target.to !== source)
                throw new CapsuleError("handoff is not available to this session");
            state.handoffs = state.handoffs.filter((item) => item.id !== id);
            await this.save(state);
        });
    }
    async clear() {
        await this.withLock(() => this.save(emptyState()));
    }
}
//# sourceMappingURL=state.js.map