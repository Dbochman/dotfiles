---
name: remote-splat
description: Prepare private capture footage and run sealed, manifest-driven COLMAP, Brush, conversion, and QA workflows on Dylan's RTX 5090 desktop. Use for video-to-photogrammetry, Gaussian-splat reconstruction, verified .sog/.ply retrieval, or an explicitly confirmed SuperSplat publish; not for general desktop administration or unconfirmed uploads.
allowed-tools: Bash(remote-splat:*)
metadata: {"openclaw":{"emoji":"🫧","requires":{"bins":["remote-splat"]}}}
---

# Remote Splat

Use `remote-splat` as the only operator interface. One sealed workflow runner
owns preflight, preparation, reconstruction, training, preview conversion,
final conversion, QA, checkpoints, dependency transitions, cancellation, and
recovery state. Do not assemble routine workflows from separate scripts or tmux
sessions, or bypass the helper with raw SSH, `scp`, `rsync`, PowerShell, WSL
paths, or direct Windows commands.

Read [references/workflow.md](references/workflow.md) when authoring or debugging
a workflow manifest. Read
[references/video-preparation.md](references/video-preparation.md) for long
footage and semantic extraction. Read
[references/host-and-toolchain.md](references/host-and-toolchain.md) only for a
reported host, runtime, or toolchain fault.

## Operator interface

Preview the exact workflow approval scope locally:

```bash
remote-splat start --job cabin-interior-v1 \
  --bundle '/absolute/local/workflow-bundle' \
  --reserve desktop-heavy --dry-run
```

After describing the manifest, inputs, declared settings, dependencies,
quality tiers, outputs, and resource reservation, start that unchanged bundle
with the returned `approvalSha256`:

```bash
remote-splat start --job cabin-interior-v1 \
  --bundle '/absolute/local/workflow-bundle' \
  --approved-workflow-sha256 '<approvalSha256>' \
  --reserve desktop-heavy
```

The manifest supplies the one run owner. The approval digest also binds the
external job dependencies and host-wide resource reservations. `start` stages
the bundle and shared helpers, freezes that job's inputs, verifies the seal on
the desktop, and launches one durable job.

Inspect, request cancellation, or retrieve a declared artifact:

```bash
remote-splat inspect --job cabin-interior-v1
remote-splat cancel --job cabin-interior-v1 --owner sol
remote-splat retrieve --job cabin-interior-v1 \
  --artifact cabin-interior.sog --destination '/absolute/local/output'
```

`inspect` is read-only and reports the run owner, reservations, recorded
process IDs, phase state, and `waitingOn` dependency or checkpoint. Do not
create diagnostic compute jobs for routine monitoring. `cancel` is
owner-bound and cooperative. The runner performs its existing cleanup checks
before recording a terminal computation state. Windows-backed workflows remain
visibly blocked after computation finishes until a human verifies the
Windows processes are absent and explicitly runs `verify-release`. Never infer
or supply that confirmation from an agent's own checks; ask the user to perform
the supervised verification and invoke the confirmation themselves.

## Safety boundary

- Keep originals immutable. A workflow bundle contains `workflow.json` and its
  phase scripts; generated data stays under its managed job.
- The dry-run approval covers every bundle/shared-runner file by size and
  SHA-256 plus dependencies and reservations. Inputs cannot change after run
  provenance exists. Recompute and disclose approval after any contract change.
- Every run declares one owner, explicit dependencies, explicit settings,
  quality tiers, artifacts, and at least one reserved resource.
- The automatic lightweight preflight must pass before expensive phases. It
  exercises the actual COLMAP feature/matcher database path, checks schema,
  probes Brush/DLL loading, and requires a stable Windows Node runtime. Its
  native tools use the sealed Windows wrapper and retain lifecycle evidence.
- Keep computation and reservation status separate. `succeeded, awaiting
  manual Windows verification` is a valid terminal result and does not permit
  another reserved job to start.
- Use the staged native Windows copy/hash helper for large Windows/WSL moves.
  Do not use metadata-preserving copies across those filesystems.
- Fetch only `.sog`, `.ply`, or generated `.webp` QA outputs. Retrieval checks
  size and SHA-256 and refuses to overwrite a different local file.
- A preview checkpoint requires a readable file plus a matching size/SHA-256
  completion receipt. It is private, explicitly non-final, and never
  promotion-eligible. Missing a target tier must be disclosed.
- Never publish, replace, delete, or change sharing on SuperSplat without fresh
  explicit confirmation naming the exact artifact and visibility.
- Treat footage, reconstructions, logs, and scene names as private household
  data. Keep operational markers free of paths, filenames, credentials, and
  household details.

## Input transport and local preparation

Use `video-probe`, `video-review`, and `video-extract` before staging long
footage. For a large file already on the Mini, use `inbox-stage`. For a
supported video directly in Dylan's Mac `Downloads`, use
`workstation-inbox-stage`; it sends directly when possible and otherwise uses
a bounded streaming relay without retaining an intermediate Mini copy. In all
cases the workflow must verify the recorded inbox hash before ingest.

Low-level `workflow-plan`, `stage`, `preflight-plan`, `plan-run`, `run`,
`progress`, `attach`, and `fetch` commands remain for debugging and recovery.
They are not the normal operator workflow. `preflight-plan` produces a sealed,
preflight-only workflow runner rather than approving the canary as a direct
standalone script.

## Publication guard

`remote-splat publish-plan --file <local.sog-or-ply>` is read-only. Present its
exact name, size, SHA-256, proposed title, and visibility, then obtain fresh
confirmation before any authenticated upload. Re-hash immediately before
upload and stop if it differs.
