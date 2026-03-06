# Dylan's Preferences

Working style preferences for Claude Code sessions.

---

## Context

**Who:** Software engineer — SRE background, currently building home automation (OpenClaw) and personal projects
**Quality bar:** Production-grade. Not "good enough" — the real thing.
**Primary work:** OpenClaw agent infrastructure, dotfiles, home automation, personal website

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
| **Session start** | Read memory + git log, provide "here's where we left off" |
| **Session end** | Full handoff - status, next steps, suggest commit |

---

**Last Updated:** March 6, 2026
