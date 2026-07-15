import { type OpenClawPluginDefinition } from "openclaw/plugin-sdk/plugin-entry";
import { type CapsuleView, type ContinuitySource } from "./state.js";
interface PluginConfig {
    imessageSession: string;
    reachySession: string;
    statePath: string;
    summaryModel: string;
    soulPath: string;
}
export declare function sourceForSession(config: PluginConfig, sessionKey?: string): ContinuitySource | null;
export declare function lastAssistantText(messages: unknown[]): string;
export declare function buildDirectVoiceContext(soul: string, view: CapsuleView): {
    revision: string;
    soul: string;
    capsule: string;
};
declare const plugin: OpenClawPluginDefinition;
export default plugin;
