---
name: desktop-compute
description: Run managed Linux, Windows, CUDA, media, data-processing, or long-running jobs on Dylan's private RTX 5090 desktop. Use when work should be transferred from the Mac mini to the desktop, executed durably in tmux, monitored, or returned as verified artifacts. Use remote-splat instead for COLMAP, Gaussian-splat training, or SuperSplat workflows.
allowed-tools: Bash(desktop-compute:*)
metadata: {"openclaw":{"emoji":"🖥️","requires":{"bins":["desktop-compute"]}}}
---

# Desktop Compute

Use `desktop-compute` for autonomous work on the private Windows desktop and
its Ubuntu WSL environment. The helper uses a restricted SSH identity whose
server-side forced command permits only fixed-root transfer, exact hash-bound
execution, bounded tmux monitoring, and verified artifact retrieval.

Do not bypass the helper with raw SSH, `scp`, `rsync`, PowerShell remoting, or
direct Tailscale addressing. The separate `ssh desktop-compute` login is for
trusted operator maintenance and is intentionally outside this skill.

Read [references/workflow.md](references/workflow.md) before preparing a first
job or choosing between Linux and Windows scope. Read
[references/host-and-recovery.md](references/host-and-recovery.md) only when
status reports that the host or a required tool is unavailable.

## Safety boundary

- Keep every input, script, log, and output inside one validated managed job.
- `stage` adds or replaces files only before immutable run provenance exists.
  Once a run is accepted, its inputs are frozen. Staging never deletes remote
  data and rejects symlinks and path traversal.
- Inspect a staged script with `plan-run` before execution. `run` accepts only
  the exact SHA-256 returned for that unchanged script.
- Every run declares one owner, zero or more completed job dependencies, and at
  least one host-wide reserved resource. Windows and splat jobs share the same
  namespace; interrupted jobs retain reservations.
- A clear user request for the described computation authorizes that exact
  hash-bound run; do not add a generic trust prompt or ask the user to repeat
  approval. Re-plan if the script changes.
- Each job uses a separate tmux session. Do not repurpose or terminate another
  job to make room.
- Fetch only files deliberately written under the job's `outputs/` directory.
  The helper verifies size and SHA-256 and will not overwrite a different local
  file.
- Job execution is powerful by design. Do not use a staged script for account
  changes, public uploads, privilege changes, broad deletion, or unrelated
  host administration unless the user explicitly requests that distinct act.
- Treat datasets, logs, models, and outputs as private. Summarize results rather
  than pasting raw private paths or logs unless Dylan asks for diagnostics.

## Standard workflow

Check the Linux workspace by default:

```bash
desktop-compute status
```

Use `--scope windows` when a PowerShell script or Windows-native GPU tool is
required:

```bash
desktop-compute status --scope windows
```

Preview and stage a file or directory:

```bash
desktop-compute stage --job video-index --source '/absolute/local/input' --dry-run
desktop-compute stage --job video-index --source '/absolute/local/input'
```

Bind a run to the staged script, then start it:

```bash
desktop-compute plan-run --job video-index --script process.sh
desktop-compute run --job video-index --script process.sh \
  --approved-sha256 '<exact hash from plan-run>' \
  --owner sol --reserve desktop-heavy
```

For Windows scope, pass `--scope windows` to both commands and use `.ps1` or
`.sh`. A job script should read only from `input/`, write deliverables to
`outputs/`, and leave operational output in the managed log.

Monitor without disturbing the run:

```bash
desktop-compute progress --job video-index
desktop-compute attach --job video-index
```

Ordinarily use `progress`; `attach` is for an operator who wants the live tmux
view. Detach with `Ctrl-b d`.

Only manifest-driven workflows support cooperative cancellation:

```bash
desktop-compute cancel --job video-index --owner sol
```

If `progress` reports `interrupted`, do not clear the reservation or start a
replacement until the recorded processes have been checked deliberately.

Retrieve one output:

```bash
desktop-compute fetch --job video-index \
  --artifact index.sqlite3 --destination '/absolute/local/output'
```

Report the verified filename, byte size, and SHA-256 along with the final job
state. The interface does not publish or send artifacts elsewhere.
