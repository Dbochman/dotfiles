# Dylan's Preferences

Working style preferences for Claude Code sessions. Updated January 12, 2026.

---

## Context

**Who:** Professional SRE / Incident Manager
**Quality bar:** Production-grade. Not "good enough" - the real thing.
**Purpose:** This repo is portfolio + learning + actual tools, but **prioritize for external reception** when there's tension between them.

---

## Anti-patterns (Universal)

These apply to any repo, not just this one:

- **No localStorage** - Use URL params, server state, or stateless approaches
- **No elaborate ASCII diagrams** - Prefer concise inline flows (e.g., `A → B → C → D`)

---

## Working Style

| Aspect | Preference |
|--------|------------|
| **Verbosity** | Moderate - explain what I'm about to do, then do it |
| **Autonomy** | Ask first - confirm before making changes, even for small fixes |
| **Decisions** | Recommend + explain - give your recommendation with reasoning |
| **Research output** | Summary only - conclusions and key findings |
| **Errors** | Error + analysis - explain why it happened, then fix |
| **Comments** | Minimal - only when logic is non-obvious |
| **Tone** | Professional - clear, respectful, occasional personality |

## Git Workflow

| Aspect | Preference |
|--------|------------|
| **Commits** | After each feature/fix - small, atomic |
| **PRs** | Narrative - include "The Journey" section for blog material |
| **Testing** | Before suggesting commit, but skip for docs-only changes |
| **Commands** | Literal - "push" = `git push`, "merge" = merge the PR. Don't assume. |

## Session Management

| Aspect | Preference |
|--------|------------|
| **Session start** | Read session-notes + git log, provide "here's where we left off" |
| **Session end** | Full handoff - status, next steps, update session notes, suggest commit |

---

**Last Updated:** January 12, 2026
