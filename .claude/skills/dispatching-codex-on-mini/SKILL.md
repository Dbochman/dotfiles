---
name: dispatching-codex-on-mini
description: Delegate multi-file or multi-repo implementation work to codex running on dylans-mac-mini via SSH. Covers the PATH wrapper needed because SSH non-interactive shells do not source nvm, the --add-dir trick for working across two repos in one session, a mandatory pre-flight checklist that prevents codex from stalling on a bad baseline, how to monitor progress without flooding the chat, and the resume-after-blocker recipe. Use when the user says "ask codex to...", "instruct codex to...", "delegate X to codex", "have codex implement...", or any similar request for autonomous implementation of a bounded engineering task.
---

# dispatching-codex-on-mini

## When to use

Trigger phrases: "ask codex to...", "instruct codex to...", "delegate X to codex", "have codex implement...". Also use proactively when the user asks for a task that spans the cabin repo + dotfiles + a LaunchAgent — that scope is the sweet spot for codex and awkward for a chat-driven back-and-forth.

Codex on the Mini is good at:

- Multi-step engineering work in Node / Python / shell inside bounded repos
- Running tests, `plutil -lint`, `gh pr create`, `git worktree add`
- Working across two repos in one session via `--add-dir`
- Stopping safely when a precondition breaks

It is not good at:

- Divining your intent from a vague prompt
- Fixing baselines you haven't committed yet
- Guessing which blockers it is authorized to clear
- Deciding PR ordering you did not specify

## Pre-flight checklist (MANDATORY)

Run every one of these before dispatching. Each item exists because a previous run stalled on it.

1. **Target branches are green.** For each repo codex will touch, on the user's local machine:
   - `git status --short` is empty (or every uncommitted change is intentional and codex is told about it)
   - `npm test` / `pytest` / `plutil -lint` — whatever the project uses — passes on `origin/main`
2. **Every path mentioned in the prompt exists on `origin/main`.** If the task says "modify `site/vol-1-no-02-draft-v2/`", grep the prompt for every file path and verify each one is present on main. Codex will not recreate missing prerequisites; it will stop and report.
3. **Node is reachable from the SSH non-interactive shell.** `ssh dylans-mac-mini 'which node'` must succeed. On the Mini today node lives at `/usr/local/bin/node` and SSH does not source `~/.zshrc`, so dispatches need `PATH=/usr/local/bin:$PATH` prefixed.
4. **Codex itself is reachable.** `ssh dylans-mac-mini 'PATH=/usr/local/bin:$PATH codex --version'` prints a version.
5. **`gh` is authenticated on the Mini for every target owner.** `ssh dylans-mac-mini 'gh auth status'`. Dylan's PAT must cover both owners if the task crosses accounts (e.g. `Dbochman/dotfiles` + `JJJennings/cabin`).
6. **A human has read the prompt as if they were codex.** Not just drafted — read it. Flag ambiguity before dispatching.

If any item fails, FIX IT FIRST. Do not dispatch and hope.

## The dispatch command

Write the task to a local file, scp it, then launch nohup codex detached:

```bash
scp task-prompt.md dylans-mac-mini:/tmp/codex-task.md

ssh dylans-mac-mini '
  PATH=/usr/local/bin:$PATH nohup codex exec \
    -C /Users/dbochman \
    --sandbox workspace-write \
    --add-dir /Users/dbochman/Repos/cabin \
    --add-dir /Users/dbochman/dotfiles \
    --skip-git-repo-check \
    -o /tmp/codex-task.final \
    "$(cat /tmp/codex-task.md)" \
    > /tmp/codex-task.log 2>&1 < /dev/null & disown
  echo "pid=$!"
'
```

Flag notes:

- `PATH=/usr/local/bin:$PATH` — see pre-flight item 3.
- `-C /Users/dbochman` — primary workspace cwd. Both repos sit under it.
- `--add-dir` — one per additional repo. Codex sandbox blocks writes outside listed dirs.
- `--sandbox workspace-write` — disk writes OK. Allows `git push` and `gh pr create` in practice; if you see network blocks, step up to `danger-full-access` only after reading the prompt again.
- `--skip-git-repo-check` — primary cwd isn't a repo, which is intentional when spanning two.
- `-o /tmp/codex-task.final` — codex writes its final summary here. Easier to grep than the full log.
- `nohup ... < /dev/null & disown` — keeps codex running after SSH closes. The `< /dev/null` matters; without it codex blocks on stdin.

## The task template

See `task-template.md` next to this file. Fill every section. Blank sections are a smell.

Signal-based "done" criteria (section EXPECTED OUTCOME) and the AUTHORIZED SIDE-FIXES section are the two parts people under-fill the first time they use the template. Lean on them.

## Monitoring

Use the Monitor tool with a filtered `tail -F`. Do not pipe the raw log — codex emits hundreds of lines per minute of context dumps.

```bash
ssh dylans-mac-mini "tail -F /tmp/codex-task.log" 2>&1 \
  | grep -E --line-buffered "^(/bin/zsh|/opt/|tokens used|---+)|.*(succeeded in|exited.*in|github\.com|Error:|FAIL|ERROR|npm test|gh pr|git push|git commit|git worktree|branch pushed|not opened|blocked)" \
  | head -200
```

Set `timeout_ms` to roughly 2× your expected run time (medium task: 2700000ms / 45 min; large: 3600000ms / 60 min — the max).

The final summary in `/tmp/codex-task.final` is written atomically on exit. Poll it with:

```bash
ssh dylans-mac-mini 'test -s /tmp/codex-task.final && cat /tmp/codex-task.final'
```

## Blockers and resume

If codex stops with blockers listed in `/tmp/codex-task.final`:

1. **Read the blocker summary.** Codex is specific about what it needed.
2. **Fix the blockers on `main`** — not on codex's branch. The blockers are baseline issues; your fix belongs upstream of the feature work.
3. **Re-dispatch.** The task prompt should already be idempotent. Codex will start fresh on a clean baseline. Expect a second full run cost; this is the price of a bad first baseline.

`codex exec resume --last` works in theory for continuing a session, but requires the same cwd and no new sessions in between. For most real-world cases, re-dispatching is simpler and more reliable than threading through resume semantics.

## Guardrails

- **Never dispatch before pre-flight passes.** Not even "quickly, just to see." Codex will stall or produce half-work.
- **Never forbid push broadly without listing authorized side-fixes.** If the task might require fixing a bad baseline (test infra, lint config, missing file), say so in the prompt under AUTHORIZED SIDE-FIXES. The default should be "codex may make a single prep commit on the feature branch and note it in the PR body."
- **Never ask codex to divine ordering.** If PR B depends on PR A, say so explicitly.
- **Never set `--sandbox danger-full-access` without rereading the prompt.** That bypasses the sandbox entirely. Only use for tasks where you trust every line of the prompt.
- **Capture the PR URLs from `-o` output**, not from the log tail. The log may truncate or be too noisy; the final file is the source of truth.

## Related

- `openai-codex-cli-usage` skill — general codex CLI usage patterns beyond this specific dispatch pattern.
- `openclaw/LAUNCHAGENTS.md` in dotfiles — if the delegated task touches a LaunchAgent, codex should match the shape used by neighbors.
