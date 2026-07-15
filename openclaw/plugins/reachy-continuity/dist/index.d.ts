import { type OpenClawPluginDefinition } from "openclaw/plugin-sdk/plugin-entry";
import { type ContinuitySource } from "./state.js";
interface PluginConfig {
    imessageSession: string;
    reachySession: string;
    statePath: string;
    summaryModel: string;
}
export declare function sourceForSession(config: PluginConfig, sessionKey?: string): ContinuitySource | null;
export declare function lastAssistantText(messages: unknown[]): string;
declare const plugin: OpenClawPluginDefinition;
export default plugin;
