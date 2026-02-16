---
name: summarize
description: Summarize any URL, YouTube video, podcast, PDF, or file into concise text. Use when asked to read an article, summarize a link, get the gist of a video or podcast, extract content from a URL, or when you need to understand what a web page or document contains.
allowed-tools: Bash(summarize-wrapper:*)
metadata: {"openclaw":{"emoji":"📝","requires":{"bins":["summarize-wrapper"]}}}
---

# Summarize

Summarize URLs, YouTube videos, podcasts, PDFs, and files via the `summarize-wrapper` CLI. Uses free OpenRouter models by default.

## Summarize a URL

```bash
summarize-wrapper "https://example.com/article"
summarize-wrapper "https://example.com" --plain
```

Always use `--plain` when you need clean text output without ANSI formatting.

## Summarize YouTube

```bash
summarize-wrapper "https://youtu.be/dQw4w9WgXcQ" --youtube auto --plain
summarize-wrapper "https://www.youtube.com/watch?v=VIDEO_ID" --plain
```

## Summarize a Podcast

```bash
# RSS feed
summarize-wrapper "https://feeds.npr.org/500005/podcast.xml" --plain

# Apple Podcasts
summarize-wrapper "https://podcasts.apple.com/us/podcast/episode/id123?i=456" --plain

# Spotify (best-effort)
summarize-wrapper "https://open.spotify.com/episode/ID" --plain
```

## Summarize Files

```bash
summarize-wrapper "/path/to/file.pdf" --plain
summarize-wrapper "/path/to/document.md" --plain
```

## Summarize Piped Content

```bash
echo "long text content" | summarize-wrapper - --plain
```

## Output Length

```bash
# Presets: short, medium, long, xl, xxl
summarize-wrapper "https://example.com" --length short --plain
summarize-wrapper "https://example.com" --length long --plain

# Character count
summarize-wrapper "https://example.com" --length 5000 --plain
```

## Extract Only (No Summary)

```bash
summarize-wrapper "https://example.com" --extract --plain
```

Returns the extracted text content without running it through an LLM.

## Common Options

- `--plain` — No ANSI/OSC rendering (recommended for agent use)
- `--length short|medium|long|xl|xxl|<chars>` — Output length
- `--extract` — Extract content only, skip summarization
- `--model <provider/model>` — Override model (default: `free` via OpenRouter)
- `--language <lang>` — Output language (`auto` matches source)
- `--json` — Machine-readable JSON output with metrics
- `--no-cache` — Skip summary cache
- `--timeout <duration>` — Request timeout (default: `2m`)

## Notes

- Default model is `free` (OpenRouter free tier, auto-selected best available)
- Config at `~/.summarize/config.json` with `OPENROUTER_API_KEY`
- Binary at `/opt/homebrew/bin/summarize-wrapper`
- Requires Node 22+ (keg-only at `/opt/homebrew/Cellar/node@22/`)
- For content extraction without LLM cost, use `--extract`
- Short content is returned as-is (use `--force-summary` to override)
