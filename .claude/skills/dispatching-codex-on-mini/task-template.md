# Codex task template

Copy this file, fill every section, save as `/tmp/codex-task.md`, dispatch per the SKILL.md command. Blank sections are a smell; fill them even if the answer is "none" or "n/a."

---

## TASK

One sentence. What state should exist after this run that does not exist now?

## EXPECTED OUTCOME

The signals that mean "done." Observable states, not deliverables.

- [ ] <specific git / gh / test / file state codex can verify>
- [ ] <specific git / gh / test / file state codex can verify>
- [ ] <specific git / gh / test / file state codex can verify>

The task is complete only when every signal is observable without further action from a human. If you cannot write a signal that way, the task is still too vague.

## CONTEXT

- Current state of the system (what exists now, what is broken, what works):
- Files codex should read first before writing:
- Related docs that explain conventions or shapes to match:
- Target device / env (host, OS, service manager):
- Version / dependency assumptions (Node 22+, Python 3.12, etc.):
- Preexisting services / ports / processes that must not conflict:

## CONSTRAINTS

- Branch names, directories, commit style (Conventional Commits? one commit vs several?)
- Allowed vs forbidden `npm install` / `brew install` / `pip install`
- Allowed vs forbidden network targets (GitHub API fine; anything else?)
- Default values / flags that must not change (e.g. existing `--host=127.0.0.1` default)
- Preexisting tests that must still pass
- Secrets handling (never read, never log, never commit)

## MUST DO

Hard requirements. Each one, if skipped, invalidates the task.

- 
- 
- 

## AUTHORIZED SIDE-FIXES

Things codex MAY touch beyond the main scope to clear its own path. Be explicit — this prevents the "codex stops on a technicality" failure mode seen in prior runs.

Default phrasing worth considering:

- "If `npm test` has pre-existing failures unrelated to this task, codex may add a single prep commit on the feature branch that fixes them. Note the fix in the PR body under 'Collateral.'"
- "If a target file path is missing from `origin/main` and the user intended it to be present, STOP and report; do not fabricate it."
- "If a dependency's documented version is older than what is installed and the newer API is clearer, codex may use the newer API and note the version floor in the PR body."

Delete the ones that don't apply; add any that do.

## MUST NOT DO

Forbidden actions. Each one, if done, invalidates the task.

- Push to `main` on any affected repo.
- Delete or rewrite history.
- Install packages outside the explicit allowlist above.
- Touch any file outside the paths named in TASK / MUST DO / AUTHORIZED SIDE-FIXES.
- Modify secrets or anything under `.ssh/`, `~/.openclaw/.secrets-cache`, or `1Password` references.

## BEHAVIOR ON FAILURE

For each failure class, specify what codex should do. The default fallback should be STOP + REPORT, not improvise.

- **Pre-flight check fails (red tests on baseline, missing prerequisite file)**: STOP. Report the exact signal that failed. Do not try to clear the blocker unless it is explicitly in AUTHORIZED SIDE-FIXES.
- **Build / test fails after codex's own changes**: FIX IT. The failure is yours. Re-run tests until green or until you can explain why the test is wrong.
- **Network blocked (push, `gh pr create`)**: Retry once with a 10-second backoff. If still blocked, STOP and report so the user can rerun with a wider sandbox.
- **Ambiguity in task**: STOP. Report the ambiguity and your best-guess reading at the end of the summary. Do not pick silently.
- **PR conflict with a branch that already exists**: STOP. Do not force-push or delete the other branch.

## OUTPUT FORMAT

The final summary (written to `-o /tmp/codex-task.final`) must contain:

- Short summary of what was built (2–4 sentences).
- Bulleted list of files touched per repo.
- Any AUTHORIZED SIDE-FIXES that were used, with the exact prep-commit SHA and one-line rationale.
- URLs of PRs or key artifacts at the very end, each on its own line.
- If STOPPED: a line `STATUS: blocked — <reason>` and nothing else below it.
