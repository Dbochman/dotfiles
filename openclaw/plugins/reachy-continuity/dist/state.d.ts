export type ContinuitySource = "imessage" | "reachy";
export interface CapsuleEntry {
    id: string;
    ts: number;
    source: ContinuitySource;
    summary: string;
    runId?: string;
}
export interface CapsuleHandoff {
    id: string;
    ts: number;
    from: ContinuitySource;
    to: ContinuitySource;
    summary: string;
}
export interface CapsuleView {
    entries: CapsuleEntry[];
    handoffs: CapsuleHandoff[];
}
export declare class CapsuleError extends Error {
}
export declare function cleanSummary(value: string): string;
export declare class CapsuleStore {
    readonly path: string;
    private readonly now;
    constructor(path: string, now?: () => number);
    private acquireLock;
    private withLock;
    private load;
    private save;
    readFor(source: ContinuitySource): Promise<CapsuleView>;
    append(source: ContinuitySource, summary: string, runId?: string): Promise<void>;
    createHandoff(from: ContinuitySource, summary: string): Promise<CapsuleHandoff>;
    consume(source: ContinuitySource, id: string): Promise<void>;
    clear(): Promise<void>;
}
