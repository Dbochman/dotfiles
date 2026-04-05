# Anthropic Auth Split: NVIDIA Proxy (MBP) + Pro OAuth (Mini)

**Status:** DONE (2026-04-05)
**Reason:** Downgraded from Claude Max to Claude Pro. Wanted to use NVIDIA inference proxy API key for Claude Code on MBP.

## Context

OpenClaw and Claude Code were both authenticated via OAuth tokens from a Claude Max subscription. After downgrading to Pro, attempted to switch everything to an NVIDIA inference proxy API key. This worked for Claude Code on MBP but **not for OpenClaw** because the NVIDIA proxy uses different model IDs (`aws/anthropic/bedrock-claude-opus-4-6`) that don't match OpenClaw's internal format (`anthropic/claude-opus-4-6`).

**Result:** Split auth model — MBP uses NVIDIA proxy, Mini stays on Pro OAuth.

## NVIDIA Inference Proxy Details

- **Key:** NVIDIA inference API key (short format, `sk-*`), NOT a standard Anthropic key (`sk-ant-api03-*`)
- **1Password:** `op://NVIDIA/Nvidia Opus 4.6 API/password`
- **Base URL:** `https://inference-api.nvidia.com`
- **Model ID:** `aws/anthropic/bedrock-claude-opus-4-6` (NOT `claude-opus-4-6`)
- **Key management:** [inference.nvidia.com/key-management](https://inference.nvidia.com/key-management)
- **Key restriction:** Keys are scoped to `default-models` — standard model IDs like `claude-opus-4-6` are rejected with `key_model_access_denied`. Must use the namespaced IDs.

## MBP Setup (Claude Code)

`~/.secrets` (chmod 600, sourced by `~/.zshrc`):
```bash
export ANTHROPIC_API_KEY="<nvidia-inference-key>"
export ANTHROPIC_BASE_URL="https://inference-api.nvidia.com"
export ANTHROPIC_MODEL="aws/anthropic/bedrock-claude-opus-4-6"
```

`~/.zshrc`:
```bash
[[ -f "$HOME/.secrets" ]] && source "$HOME/.secrets"
```

After `claude auth logout` (to clear stale Max OAuth from macOS keychain), Claude Code picks up the env vars and shows "API Usage Billing" in the header.

## Mini Setup (OpenClaw) — Unchanged

- Auth mode: `"mode": "token"` in `openclaw.json`
- OAuth refresh: `ai.openclaw.oauth-refresh` every 6hr (re-enabled)
- Model: `anthropic/claude-opus-4-6` (OpenClaw's internal format)
- Usage dashboard: `/api/oauth/usage` with Pro subscription token

## Why OpenClaw Can't Use NVIDIA Proxy

OpenClaw hardcodes its model ID format as `anthropic/claude-opus-4-6`, which maps to API model ID `claude-opus-4-6`. The NVIDIA proxy rejects this — it requires `aws/anthropic/bedrock-claude-opus-4-6`. There's no config in OpenClaw to override the API-level model ID independently of the internal routing format.

## BlueBubbles SSRF Fix (discovered during this work)

OpenClaw 2026.4.2 introduced SSRF protection that blocks plugin HTTP calls to localhost/private IPs.

**Fix:** Added `"allowPrivateNetwork": true` to BlueBubbles channel config in `openclaw.json`.

**References:** openclaw/openclaw#57181, openclaw/openclaw#60715

## Gotchas Discovered

1. **OpenClaw auth mode enum:** `"api_key"` (underscore), NOT `"api-key"` (hyphen). Wrong value causes config validation failure and gateway refuses to start.
2. **NVIDIA proxy model IDs:** Must use namespaced format (`aws/anthropic/bedrock-claude-opus-4-6`). Standard IDs rejected.
3. **`claude auth logout` required:** After switching from OAuth to API key, must explicitly log out — cached OAuth in macOS keychain takes priority over `ANTHROPIC_API_KEY` env var.
4. **`op read` in `.zshrc` unreliable:** 1Password CLI may not be signed in during shell init, causing silent failure. Use a flat secrets file instead.
5. **`allowPrivateNetwork`:** Required since OpenClaw 2026.3.28 for any channel plugin connecting to localhost/private IPs.

## Files Modified

| File | Final State |
|------|-------------|
| `openclaw/openclaw.json` | `mode: "token"` (reverted), `allowPrivateNetwork: true` (kept) |
| `openclaw/openclaw-remote.json` | `mode: "token"` (reverted) |
| `openclaw/launchagents/ai.openclaw.oauth-refresh.plist` | Re-enabled |
| `openclaw/bin/openclaw-refresh-secrets` | No ANTHROPIC vars (reverted) |
| `openclaw/bin/usage-snapshot.sh` | OAuth utilization restored |
| `~/.zshrc` (MBP) | Sources `~/.secrets` |
| `~/.secrets` (MBP) | ANTHROPIC_API_KEY + BASE_URL + MODEL |
