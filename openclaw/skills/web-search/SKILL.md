---
name: web-search
description: Search the web for current information, news, facts, and answers. Use when asked questions about current events, needing to look something up, finding websites, researching topics, or when you need up-to-date information beyond your training data.
allowed-tools: Bash(websearch:*)
metadata: {"openclaw":{"emoji":"🔍","requires":{"bins":["websearch","ddgr"]}}}
---

# Web Search

Search the web via the `websearch` CLI. Uses DuckDuckGo (ddgr) as primary provider and Tavily API as fallback with AI-powered answers.

## Quick Search

```bash
websearch "best restaurants in Boston"
websearch "weather tomorrow Boston"
websearch "python requests library docs"
```

Default behavior: tries ddgr first (free, unlimited), falls back to Tavily if ddgr fails.

## Search with Tavily AI Answer

```bash
websearch tavily "what is the capital of France"
websearch tavily "how does photosynthesis work"
```

The `tavily` subcommand uses the Tavily API and includes an AI-generated answer along with source results.

## Options

```bash
# Limit number of results
websearch -n 10 "search query"

# Restrict to a specific site (ddgr only)
websearch -s reddit.com "best headphones"

# Force a specific provider
websearch -p tavily "search query"
websearch -p ddgr "search query"

# Tavily topic filter
websearch tavily -t news "tech layoffs"

# Include AI answer with default search
websearch --answer -p tavily "search query"

# JSON output
websearch --json "search query"
```

## Check Provider Status

```bash
websearch status
```

Shows whether ddgr and Tavily are available and configured.

## Global Flags

- `--json` — Machine-readable JSON output
- `-n, --num N` — Number of results (default: 5, max: 20)
- `-s, --site DOMAIN` — Restrict to domain (ddgr only)
- `-p, --provider NAME` — Force provider: `ddgr` or `tavily`

## Providers

| Provider | Cost | API Key | Best For |
|----------|------|---------|----------|
| **ddgr** (default) | Free | None | General searches, quick lookups |
| **Tavily** (fallback) | 1,000/month free | Required | AI-optimized results, answers |

## Notes

- ddgr is always tried first (free, no rate limits)
- Tavily is used as fallback or when explicitly requested
- Tavily uses its exact cached environment key; its vault/item identifiers remain in the owner-only refresh seed
- The gateway loads secrets from `~/.openclaw/.secrets-cache` at startup and never calls `op`
- To add or rotate the Tavily key, run `openclaw-refresh-secrets --interactive` from an attended terminal
- If the key is absent at runtime, skip Tavily or fail closed; do not invoke `op`
- Logs are written to `~/.openclaw/logs/websearch.log`
