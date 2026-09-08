---
name: remote-splat
description: Prepare long capture videos into reviewable semantic clips and ordered modeling frames, then use the private RTX 5090 desktop to stage photo datasets, run hash-approved COLMAP or Brush Gaussian-splat jobs, monitor them through tmux, validate and retrieve .sog/.ply artifacts, and prepare an explicitly confirmed SuperSplat publish. Use for video-to-photogrammetry preparation, remote reconstruction, and Gaussian-splat work; not for general desktop administration, unrelated compute jobs, or unconfirmed public uploads.
allowed-tools: Bash(remote-splat:*)
metadata: {"openclaw":{"emoji":"🫧","requires":{"bins":["remote-splat"]}}}
---

# Remote Splat

Use the `remote-splat` helper for every transfer and remote job operation. It
specializes the shared `desktop-compute` job layer with a private splat root,
restricted forced-command SSH identity, splat-specific output validation, and
the SuperSplat publication guard. Do not bypass it with raw SSH, `scp`,
`rsync`, PowerShell, WSL paths, or direct Windows commands.

Read [references/host-and-toolchain.md](references/host-and-toolchain.md) only
when diagnosing the host or preparing a new job script. Read
[references/workflow.md](references/workflow.md) before changing reconstruction,
training, export, validation, or publishing behavior.
Read [references/video-preparation.md](references/video-preparation.md) before
reviewing long footage, defining semantic segments, or extracting frames.

## Safety boundary

- Status, planning, progress, and artifact inspection are read-only.
- `stage` may create or add files only inside one validated remote job.
- For multi-gigabyte single files, `inbox-stage` uses private Taildrop to the
  fixed desktop host. The approved job script must verify the returned SHA-256
  before copying the inbox file into its managed job root; never execute an
  inbox file in place.
- A remote script must be staged, inspected, and approved by its exact SHA-256
  before `run`; never infer or reuse approval after the file changes.
- Each job gets its own `splat-<job>` tmux session. Do not kill another session,
  process, or job to make room.
- A job name records one exact script run and its tmux session remains as
  provenance after exit. Use distinct job names for audit, preparation,
  connectivity, training, and validation phases; do not reuse a completed job
  name for a different script.
- Fetch only `.sog`, `.ply`, or generated `.webp` visual-QA outputs through the
  helper. It verifies SHA-256 after transfer and refuses to overwrite a
  different local file. Publication remains limited to `.sog` and `.ply`.
- Never publish, replace, delete, or change sharing on SuperSplat without a
  fresh explicit confirmation naming the exact artifact and visibility.
- Treat photos, reconstructions, logs, and scene names as private household
  data. Do not expose local/remote paths, account details, or raw logs in chat
  unless Dylan explicitly asks for diagnostics.
- Keep source videos immutable. Review and extraction outputs must use new or
  empty directories; never overwrite or trim an original recording.

## Workflow

### 0. Prepare long-form footage

Probe each video, then build one private review package for all related footage:

```bash
remote-splat video-probe --source '/absolute/local/trail-one.mov'
remote-splat video-review \
  --source '/absolute/local/trail-one.mov' \
  --source '/absolute/local/trail-two.mov' \
  --output '/absolute/local/cabin-video-review' \
  --interval-seconds 30 --proxy
```

Open the generated `index.html` or scrub its metadata-free 720p proxies. Copy
`segments.template.json` to a working manifest and add deliberate lowercase
segment names plus start/end timecodes. Automatic scene suggestions may help
with edited footage, but they do not understand semantic areas such as yard,
interior, or trails.

Validate before extraction, then write frame-accurate silent clips and ordered
modeling frames to a new directory:

```bash
remote-splat video-extract --manifest '/absolute/local/segments.json' \
  --output '/absolute/local/cabin-video-extract' --dry-run
remote-splat video-extract --manifest '/absolute/local/segments.json' \
  --output '/absolute/local/cabin-video-extract'
```

Review the extracted frames for blur, occlusion, repetitive stationary views,
and weak transitions before staging them. Preserve overlapping boundary frames
between neighboring semantic segments so COLMAP can connect them.

### 1. Check the host

```bash
remote-splat status
```

Require `ok: true`, the expected WSL host/user, an available RTX 5090, and the
needed toolchain components. If the bridge is unavailable, report that concise
fault and use the canonical remote-access recovery documentation; do not open a
firewall port or weaken SSH.

### 2. Stage one job

Choose a short descriptive lowercase job name. Preview if useful, then stage:

```bash
remote-splat stage --job cabin-refresh --source '/absolute/local/dataset' --dry-run
remote-splat stage --job cabin-refresh --source '/absolute/local/dataset'
```

The source may be one file or directory. The helper copies into the job's
`input/` directory without deleting remote files. Inspect or create the job's
`.sh` or `.ps1` script locally before staging it too.

For an unusually large single source file, use the desktop's direct Tailscale
path and record the returned inbox name and digest in the preparation script:

```bash
remote-splat inbox-stage --job cabin-refresh --source '/absolute/local/video.MOV' --dry-run
remote-splat inbox-stage --job cabin-refresh --source '/absolute/local/video.MOV'
```

The preparation script must hash-verify and ingest the inbox file before it is
decoded or otherwise used. Current Windows Tailscale clients receive Taildrop
files in the interactive user's `Downloads` directory; bind the exact inbox
name and digest rather than scanning or draining unrelated inbox contents.

### 3. Bind approval to the script

```bash
remote-splat plan-run --job cabin-refresh --script retrain.ps1
remote-splat run --job cabin-refresh --script retrain.ps1 \
  --approved-sha256 '<exact hash returned by plan-run>'
```

Before `run`, summarize the script, important input/output directories,
COLMAP/Brush settings, expected runtime, and the returned hash. A request to
perform that clearly described run is sufficient approval. If the helper says
the hash changed, inspect and plan again; do not substitute a new hash silently.

### 4. Monitor durably

```bash
remote-splat progress --job cabin-refresh
remote-splat attach --job cabin-refresh
```

Use `progress` for ordinary checks. `attach` is interactive and should be used
only when an operator wants the tmux view. Detach with `Ctrl-b d`; do not stop
the job. The current reverse bridge is TCP-only, so Mosh is not available.

### 5. Retrieve outputs

```bash
remote-splat fetch --job cabin-refresh \
  --artifact cabin.sog --destination '/absolute/local/output'
remote-splat fetch --job cabin-refresh \
  --artifact cabin.compressed.ply --destination '/absolute/local/output'
remote-splat fetch --job cabin-validation \
  --artifact validation/orbit-01.webp --destination '/absolute/local/output'
```

Report the verified filename, byte size, and SHA-256. Prefer a validated `.sog`
for efficient viewing/sharing and retain the `.ply` when future conversion or
editing is likely.

### 6. Guard publication

Prepare the exact local artifact:

```bash
remote-splat publish-plan --file '/absolute/local/output/cabin.sog'
```

This command never uploads. Present its exact filename, size, SHA-256, proposed
SuperSplat title, and intended visibility, then obtain fresh explicit
confirmation. Only after confirmation may an authenticated browser upload that
unchanged file. Verify the resulting title and visibility before reporting
success. Stop on an account mismatch, challenge, changed hash, ambiguous
receipt, or unexpected public visibility; never retry an ambiguous publish.
