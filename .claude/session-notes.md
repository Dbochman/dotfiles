# Session Notes

This file captures context that might not fit in commits or PRs—the "we tried X but realized Y" moments, observations about patterns, and other notes that could inform future blog posts.

Append to this file at the end of sessions when something notable happened. Keep entries brief. Date each entry.

---

## 2026-01-10

Built the auto-blog system today. The hook was the easy part—the harder insight was that good blog posts require good source material, which means capturing context at the moment it exists (in commits, PRs, and now here).

Dylan's instinct to add commit message guidance was right. The `[blog]` tag gives me a manual override when something is clearly worth writing about. The `[blog:tag-name]` grouping solves the multi-session problem.

Three pieces now feed into posts: commit messages (signals), PR descriptions (narrative), session notes (context). The hook ties them together.

---

## 2026-01-10 (continued)

Expanded the blog system with three features Dylan suggested:
- `[blog:tag-name]` for grouping commits across sessions
- Post interlinking guidance in the style guide
- This session notes file

Also fixed redundant `# Title` in all posts—title now comes only from frontmatter. Dylan picked "The Blog That Writes Itself" as the title for the auto-blog post.

The system feels complete now. We'll see how it performs when the hook actually fires.

---

## 2026-01-12

Big day for the projects page. Shipped the infrastructure (PR #88) plus two interactive SRE tools:

1. **SLO Uptime Calculator** (PR #89) - Helps engineers understand what SLA they can realistically achieve given their response times. Two modes: "What can I achieve?" and "Can I meet this target?" The visibility toggles for response phases were a nice addition—lets you exclude pre-diagnosis overhead from calculations.

2. **Status Page Update Generator** (PR #91) - Templates for professional incident communication. Four phases (Investigating → Identified → Monitoring → Resolved) with severity-aware wording. The insight here: most teams struggle with status page messaging because they don't have templates ready.

Also added author field to blog posts (PR #92). Now you can filter by Claude vs Dylan. All existing posts marked as Claude-authored.

Pattern worth noting: The projects page uses a registry pattern with lazy-loaded components—same architecture we used for blog posts. Cross-navigation between blog and projects pages makes the site feel more cohesive.

**Planning docs consolidation:** Merged `PLANNING_DOCS_INDEX.md` and `FUTURE_WORK_ROADMAP.md` into single `ROADMAP.md`. The two files had significant overlap (both tracked "what's done" and "what's next"). Now there's one place to update. Also moved `BLOG_FEATURE_PLAN.md` to `completed-projects/` since it was done but hadn't been archived.

**Meta-improvements to Claude collaboration:**
- Created `.claude/preferences.md` via interview - captures Dylan's working style (moderate verbosity, ask before changes, atomic commits, narrative PRs, full session handoffs)
- Added session start guidance to CLAUDE.md - read session-notes + git log, provide "where we left off" summary
- Removed unused Gemini CLI section from CLAUDE.md
- Created `docs/decisions/` for ADRs with 3 retrospective records (MDX precompilation, projects registry, docs consolidation)

The preferences file is an interesting experiment—persisting working style preferences across sessions so I don't have to relearn them.

Later moved preferences to `~/.claude/preferences.md` (global) to avoid cluttering repo history with meta-work. Key insight: working style preferences are universal, session notes are project-specific.

---

## 2026-01-13

**Lighthouse + Accessibility deep dive.** Started by adding project pages to Lighthouse audits—discovered accessibility issues we hadn't noticed before.

Three pages failed thresholds:
- Blog (94 a11y): Select dropdowns missing aria-labels
- Uptime Calculator (92 a11y): Sliders and progress bars missing aria-labels, heading order skipped h2
- On-Call Coverage (89 a11y): Same select issue, plus color contrast failure (emerald-500 with white text = 3.76:1, needs 4.5:1)

**Fixes applied:**
- Added `aria-label` to all Select, Slider, and Progress components
- Modified `CardTitle` to accept `as` prop for proper heading hierarchy (h1→h2→h3)
- Changed `bg-emerald-500` → `bg-emerald-700` across oncall components for WCAG AA contrast compliance

**Pattern worth noting:** The shadcn/ui CardTitle component hardcodes `<h3>`. Adding an `as` prop was a minimal change that enables proper document structure. Same pattern could apply to other heading components.

**Color contrast insight:** Tailwind's -500 shades often don't meet WCAG AA (4.5:1) for white text. The -700 shades generally do. Worth checking anytime white text appears on colored backgrounds.

Also merged the previous oncall-coverage improvements branch which included:
- Rich tooltips with UTC/local times across all heatmaps
- Intl API for proper DST handling in timezone conversions
- US Daytime model rebalancing and dedicated BusinessHoursTimeline component

---

## 2026-01-13 (continued)

**Analytics Dashboard shipped (PR #96).** Built an unlisted `/analytics` page that visualizes GA4, Search Console, and Lighthouse data from existing collection workflows.

Key features:
- Overview cards: Sessions (7d), Lighthouse Perf, Impressions, Bounce Rate
- Three tabs: Traffic (sessions trend, device breakdown, top pages), Performance (Lighthouse scores, Core Web Vitals), Search (clicks/impressions trend)
- Traffic sources tracking added to GA4 script (will populate on next run)
- Custom tooltips with Tailwind classes for dark mode support (Recharts inline styles don't resolve CSS variables)

**Codex code reviews captured two good catches:**
1. Divide-by-zero in session trend calculation when previous period has 0 sessions
2. Error states unreachable because fetchJson swallowed all failures as "missing data"

Fixed both—now 404s show yellow warnings, actual errors (500s, network) show red error state.

**Nav cleanup:** Removed Experience, Goals, Contact links. Nav now just shows Projects and Blog.

**Footer standardization:** Created reusable `Footer` component and applied to Blog, Projects, and Project detail pages. Kept custom footers for BlogPost (author info) and Runbook (maintainer info).

---

## 2026-01-16 (SLO Tools Polish)

**Slider magnetism pattern:** When using sliders for values with common presets (like SLO percentages), add snap points that pull the slider toward round numbers. Implementation in `src/lib/slo/presets.ts`:

```typescript
export function snapToPreset(value: number): number {
  for (const snapPoint of SNAP_POINTS) {
    const threshold = snapPoint >= 99.9 ? 0.015 : 0.1;
    if (Math.abs(value - snapPoint) <= threshold) {
      return snapPoint;
    }
  }
  return value;
}
```

Use adaptive thresholds - tighter for high-precision values where small differences matter. Call it in the slider's `onValueChange` handler.

**Flexible input ranges:** For SLO tools, the text input accepts 0-99.999% while sliders focus on realistic ranges (90-99.999% or 99-99.999%). This lets power users enter any value while keeping the slider UX focused.

---

## 2026-01-16

**Cloudflare Pages preview deployments.** Set up branch preview infrastructure so PRs get unique preview URLs automatically.

Key details:
- **Project:** `personal-website-adg.pages.dev`
- **Branch URLs:** `<branch>.personal-website-adg.pages.dev`
- **Build command:** `npm run build:preview` (new script that skips Playwright prerendering—CF environment doesn't have browsers installed)
- **Preview banner:** Yellow sticky banner appears on all preview builds via hostname detection (`*.pages.dev`)

Architecture decision: Dual hosting—GitHub Pages for production, Cloudflare Pages for previews. CF Pages has built-in GitHub PR comments, so we removed a redundant custom workflow.

**Environment detection pattern** (`src/lib/env.ts`):
```typescript
export const isPreview = window.location.hostname.endsWith('.pages.dev');
export const showPreviewBanner = isPreview;
```

**Documentation updates:** Updated both `src/data/runbook.ts` and `docs/OPERATIONS_MANUAL.md` with the new CF Pages infrastructure, troubleshooting for preview deployment issues, and MCP testing workflows for previews.

**New project idea:** Added "Error Budget Burndown" to kanban Ideas column—visualize error budget consumption over time. Complements existing SLO Calculator.

**Cleanup:** Disconnected kanban-save-worker from GitHub integration in Cloudflare—it was triggering failed builds on every commit since the worker code isn't in this repo.

---

## 2026-01-20: Codex CLI Deep Dive

### Overview

Explored OpenAI Codex CLI (v0.73.0) for CI code review integration and MCP server setup. Key goal: clean up verbose Codex review output for PR comments.

### Codex CLI Commands

| Command | Purpose |
|---------|---------|
| `codex review --base origin/main` | PR review against base branch |
| `codex exec "prompt"` | Non-interactive task execution |
| `codex exec --json` | JSONL event stream output |
| `codex exec --output-schema schema.json` | **Structured JSON output** |
| `codex mcp-server` | Run as MCP server for Claude Code |
| `codex resume --last` | Resume previous session |

### Structured Output (Game Changer for CI)

Instead of parsing verbose text output with awk, use `--output-schema` for clean JSON:

```bash
codex exec --sandbox read-only \
  --output-schema review-schema.json \
  -o findings.json \
  "Review git diff against origin/main"
```

**Schema requirements are strict:**
- All objects need `"additionalProperties": false`
- ALL properties must be in `"required"` array (no optional fields)
- Nested objects have same requirements

Working schema example:
```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "summary": { "type": "string" },
    "issues": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "severity": { "type": "string", "enum": ["P1", "P2", "P3"] },
          "title": { "type": "string" },
          "file": { "type": "string" },
          "lines": { "type": "string" },
          "description": { "type": "string" }
        },
        "required": ["severity", "title", "file", "lines", "description"]
      }
    },
    "verdict": { "type": "string", "enum": ["APPROVE", "REQUEST_CHANGES", "COMMENT"] }
  },
  "required": ["summary", "issues", "verdict"]
}
```

### MCP Server Integration

Codex can be a tool for Claude Code via MCP:

**Config** (`~/.claude/settings.json`):
```json
{
  "mcpServers": {
    "codex": {
      "type": "stdio",
      "command": "codex",
      "args": ["-m", "gpt-5.2-codex", "mcp-server"]
    }
  }
}
```

**Exposed tools:**
1. `codex` - Start session with `prompt`, `sandbox`, `cwd`, `model`, `developer-instructions`
2. `codex-reply` - Continue conversation with `conversationId`, `prompt`

Requires Claude Code restart to load. Then accessible as `mcp__codex__codex()`.

### Codex Config

Location: `~/.codex/config.toml`

```toml
model = 'gpt-5-codex'

[profiles.groq-gpt]
model = 'openai/gpt-oss-120b'
model_provider = 'groq'

[projects.'/path/to/repo']
trust_level = 'trusted'
```

### Sandbox Modes

| Mode | Description |
|------|-------------|
| `read-only` | Can't modify files |
| `workspace-write` | Can modify workspace only |
| `danger-full-access` | Full system access |

### Bug Found by Codex Self-Review

Using structured output, Codex found a bug in our own Codex integration:

**Issue:** `grep -q "no issues"` doesn't match `"No issues found."` (capital N)
**Fix:** Use `grep -iq` for case-insensitive matching

This is a nice example of Codex catching real bugs when given structured output constraints.

### PRs from Today

- **PR #177**: Visual regression testing with Playwright (14 tests, CI workflow)
- **PR #179**: Clean Codex output and fix issues
  - AWK filtering to extract only findings from verbose output
  - Fix checkout ref for workflow_run events (`head_sha` vs `github.ref`)
  - Fix snapshot path template (`testFileName` vs `testFilePath`)
  - Case-insensitive grep fix
  - Kanban board update

### Next Steps

1. Restart Claude Code to load Codex MCP server
2. Test `mcp__codex__codex` delegation
3. Consider replacing AWK filtering with structured output schema in CI
4. Explore multi-turn conversations with `codex-reply`

---

## 2026-01-24: Codex Workflow + Dotfiles Setup

**PR #204 merged** - Interactive blog components (MTTRBuilder, SeveritySelector, Chart). Codex review flagged a11y issues:
- Missing `aria-label` on custom sliders
- Missing `aria-pressed` on toggle buttons
- False positive on RSS/sitemap (post was actually present)

**Pre-push hook timeout root cause:** The `codex review` command in `.husky/pre-push` was taking 3+ minutes (xhigh reasoning effort), causing GitHub's SSH connection to timeout (~60s idle limit). Error message was misleading: "Connection to github.com closed by remote host" - looks like a network issue but was actually the slow hook.

**Fix:** Removed pre-push hook entirely—CI already runs Codex reviews on PRs. Created skill for this: `~/.claude/skills/git-pre-push-hook-ssh-timeout/SKILL.md`

**Codex speed optimization:** Created `codex-quick` wrapper script that uses `model_reasoning_effort="medium"` instead of xhigh for fast local checks:
```bash
~/.local/bin/codex-quick review --uncommitted  # Fast
codex review --uncommitted                      # Thorough (CI)
```

**Dotfiles repo created** (`github.com/Dbochman/dotfiles`):

| Category | Files |
|----------|-------|
| Shell | zshrc, gitconfig |
| Codex | .codex/config.toml, .local/bin/codex-quick |
| Claude Code | CLAUDE.md, preferences.md, settings.json |
| Skills | 14 skills (debugging patterns, gotchas) |
| Commands | rams, ui-skills, web-interface-guidelines |
| Hooks | no-rm.mjs, continuous-learning-activator.sh |
| Plugins | marketplace refs, setup-plugins.md |

**Install on new machines:**
```bash
git clone git@github.com:Dbochman/dotfiles.git ~/dotfiles
~/dotfiles/install.sh
# Then: /plugin install claude-hud
```

**Key insight:** Claude Code plugins can't be file-copied (version checking, path resolution), but marketplace refs and settings can be symlinked. Plugin install commands still need to run in Claude Code.

---
