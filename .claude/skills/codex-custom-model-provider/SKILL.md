---
name: codex-custom-model-provider
description: |
  Configure OpenAI Codex CLI to use custom model providers like Groq, OpenRouter, or other
  OpenAI-compatible APIs. Use when: (1) Getting 401 errors when trying custom providers,
  (2) Codex profiles not loading correctly, (3) Need to route Codex to non-OpenAI endpoints,
  (4) wire_api errors or "responses" endpoint not found. Covers env_key auth, wire_api
  selection, and the -c flag workaround for profiles.
author: Claude Code
version: 1.0.0
date: 2026-01-24
---

# Codex Custom Model Provider Configuration

## Problem

Configuring OpenAI Codex CLI (v0.89+) to use third-party model providers like Groq, OpenRouter,
or other OpenAI-compatible APIs fails with auth errors or wrong endpoints.

## Context / Trigger Conditions

- 401 "Invalid API Key" when using custom base URLs
- 403 permission errors (auth working, model blocked)
- Codex hitting `/responses` endpoint instead of `/chat/completions`
- `-p profile_name` flag not loading custom provider settings
- Need to use Groq, Together, Anyscale, or other OpenAI-compatible providers

## Solution

### 1. Config Structure (`~/.codex/config.toml`)

```toml
[model_providers.groq]
base_url = "https://api.groq.com/openai/v1"
name = "Groq"
wire_api = "chat"        # CRITICAL: Groq doesn't support "responses" API
env_key = "GROQ_API_KEY" # Environment variable containing the API key
```

### 2. Key Settings Explained

| Setting | Purpose | Common Values |
|---------|---------|---------------|
| `base_url` | API endpoint | Provider's OpenAI-compatible URL |
| `wire_api` | API format | `"chat"` for most providers, `"responses"` for OpenAI only |
| `env_key` | Auth env var | Name of env var holding API key |
| `name` | Display name | Human-readable provider name |

### 3. Profile Definition (Optional)

```toml
[profiles.groq-gpt]
model = "openai/gpt-oss-120b"
model_provider = "groq"
```

### 4. Usage - The `-c` Flag Workaround

**Important:** In Codex v0.89, the `-p profile` flag may not load custom providers correctly.
Use the `-c` flag to explicitly set the provider:

```bash
# This may NOT work:
codex -p groq-gpt exec "prompt"

# This DOES work:
export GROQ_API_KEY="your-key"
codex -c 'model_provider="groq"' -m "openai/gpt-oss-120b" exec "prompt"
```

### 5. Common Provider Configs

**Groq:**
```toml
[model_providers.groq]
base_url = "https://api.groq.com/openai/v1"
name = "Groq"
wire_api = "chat"
env_key = "GROQ_API_KEY"
```

**OpenRouter:**
```toml
[model_providers.openrouter]
base_url = "https://openrouter.ai/api/v1"
name = "OpenRouter"
wire_api = "chat"
env_key = "OPENROUTER_API_KEY"
```

## Verification

1. Check provider is recognized:
   ```bash
   RUST_LOG=debug codex -c 'model_provider="groq"' -m "model-name" exec "test" 2>&1 | grep provider
   ```

2. Verify correct endpoint:
   - 401 + hitting correct URL = auth issue (check env var)
   - 403 = auth works, model permissions issue
   - 404 on `/responses` = need `wire_api = "chat"`

## Example

```bash
# Set API key from 1Password
export GROQ_API_KEY=$(op read "op://Private/GroqAPI Credentials/credential")

# Run with Groq provider
codex -c 'model_provider="groq"' -m "openai/gpt-oss-120b" exec --sandbox read-only "What model are you?"
```

## Notes

- `wire_api = "chat"` shows deprecation warning but is required for non-OpenAI providers
- The Responses API (`/responses`) is OpenAI-specific; most providers only support Chat Completions
- Model availability depends on provider project settings (e.g., Groq console limits)
- Machine-specific settings (like `[projects]` trust levels) should be added manually after install

## References

- [Codex Advanced Configuration](https://developers.openai.com/codex/config-advanced/)
- [Codex CLI Reference](https://developers.openai.com/codex/cli/reference/)
