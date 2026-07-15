import { type OpenClawPluginDefinition } from "openclaw/plugin-sdk/plugin-entry";
import { type CapsuleView, type ContinuitySource } from "./state.js";
interface PluginConfig {
    imessageSession: string;
    imessageTarget: string;
    reachySession: string;
    statePath: string;
    summaryModel: string;
    identityPath: string;
    soulPath: string;
    userPath: string;
}
export declare function sourceForSession(config: PluginConfig, sessionKey?: string): ContinuitySource | null;
export declare function remembersExplicitly(prompt: string): boolean;
export declare function lastAssistantText(messages: unknown[]): string;
export declare function buildDirectVoiceContext(identity: string, soul: string, user: string, view: CapsuleView): {
    revision: string;
    identity: string;
    soul: string;
    user: string;
    capsule: string;
};
declare const plugin: OpenClawPluginDefinition;
export default plugin;
