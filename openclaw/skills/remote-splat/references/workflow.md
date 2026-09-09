# Manifest-driven reconstruction workflow

Load this reference when authoring or debugging a remote-splat workflow. The
supported operator path is `start`, `inspect`, `cancel`, and `retrieve`; the
runner keeps the individual phases internal for diagnosis and reproducibility.

## Workflow bundle

A bundle contains `workflow.json` plus the scripts named by its phases. Every
file is included in the dry-run approval inventory. The helper also seals the
shared runner, preflight, and Windows I/O helper into that inventory.

Minimal shape:

```json
{
  "schemaVersion": 1,
  "job": "cabin-interior-v1",
  "owner": "sol",
  "settings": {
    "extractor": "sift",
    "matcher": "sift-bruteforce",
    "iterations": 30000,
    "maxResolution": 1900,
    "maxSplats": 6000000,
    "shDegree": 3,
    "seed": 42
  },
  "dependencies": {
    "colmap": "4.2.0",
    "brush": "0.3.0",
    "splatTransform": "pinned-lockfile",
    "baseModel": "sha256:<digest>"
  },
  "qualityTiers": {
    "target": {"minimumRegisteredViews": 300},
    "plausibleCandidate": {"minimumRegisteredViews": 248},
    "experimentalOnly": {"promotionEligible": false}
  },
  "phases": [
    {"name": "prepare", "script": "prepare.sh"},
    {"name": "reconstruct", "script": "reconstruct.sh",
     "dependsOn": [{"phase": "prepare", "state": "succeeded"}]},
    {"name": "train", "script": "train.sh", "mode": "background",
     "dependsOn": [{"phase": "reconstruct", "state": "succeeded"}]},
    {"name": "preview", "script": "convert-preview.sh",
     "dependsOn": [{"phase": "train", "state": "started"}],
     "waitForCheckpoint": {"directory": "outputs/checkpoints",
       "prefix": "scene_", "suffix": ".ply", "minimumStep": 5000}},
    {"name": "convert", "script": "convert-final.sh",
     "dependsOn": [{"phase": "train", "state": "succeeded"}]},
    {"name": "validate", "script": "validate.sh",
     "dependsOn": [{"phase": "convert", "state": "succeeded"}]}
  ],
  "artifacts": [
    {"name": "preview", "path": "preview.sog", "required": false},
    {"name": "final", "path": "cabin-interior.sog", "required": true}
  ]
}
```

The runner rejects undeclared settings/dependencies, malformed tiers, missing
scripts, dependency cycles, unsafe paths, and changed approval inputs. It
automatically injects a successful `preflight` dependency into every phase.
Phase scripts start in the managed job root and must use `input/`, `outputs/`,
and `state/` relative paths.

`foreground` phases serialize with one another. A `background` phase may keep
training while a foreground preview converts the first readable checkpoint.
Checkpoint steps are integers, so `scene_05000.ply` and `scene_5000.ply` both
mean step 5000. A checkpoint is ready only after its PLY structure is readable
and `<checkpoint>.complete.json` contains its exact `sizeBytes` and `sha256`.
Write that receipt after the checkpoint is closed. The runner records waits in
each phase's `waitingOn` field rather than spawning inspection jobs.

On success, `outputs/run-manifest.json` records the exact approval scope and
input hashes, manifest settings and declared dependencies, external job
dependencies, owner, resource reservations, phase results, and output artifact
hashes. Cancellation is cooperative and owner-bound. A vanished runner with no
exit receipt is `interrupted`; its reservation stays held until an operator
verifies recorded processes and performs deliberate recovery.

Windows phases run through the sealed native-process wrapper. Shell phases
receive `OPENCLAW_NATIVE_PID_FILE`; every Windows executable they launch must
use the same helper, as the built-in canary does. Its durable receipt remains
`running` until a bounded Windows process-tree scan proves the executable left
no live descendants, then atomically becomes `complete` with
`treeCleanupVerified: true`. A missing or deleted receipt is not proof.
Cancellation requires successful `taskkill /T`, verifies the native PID is
absent, then verifies the complete WSL process group is gone. Resource release
depends on verified cleanup rather than workflow success.

## Lightweight preflight and file operations

Preflight runs before expensive preparation or GPU work. It creates two tiny
synthetic images inside the job, exercises the actual COLMAP SIFT extraction
and `SIFT_BRUTEFORCE` matcher path, validates the SQLite schema and counts with
`quick_check`, probes Brush/DLL loading, and requires the stable Windows Node
runtime needed by the converter. A failure stops the workflow before large
copies, reconstruction, or training.

The low-level `preflight-plan` command builds a sealed preflight-only workflow;
it never returns a direct canary launch. Before deploying a lifecycle change,
run one bounded live Windows canary and verify both the completed native receipt
and released reservation. Local mocks and contract checks do not replace that
gate.

For large Windows/WSL file moves, call the staged `windows_io.ps1` helper from
a phase. It uses native `[System.IO.File]::Copy`, hashes source and temporary
copies, and atomically promotes only a matching destination. Do not request
metadata preservation across DrvFS, and do not re-read an already verified
multi-gigabyte source merely to recreate the same receipt.

## Reconstruction and incremental extension

Keep original photos immutable. Separate source images, COLMAP databases,
sparse models, checkpoints, evaluation renders, and final exports. Record
source counts, semantic segments, feature/matcher settings, registered views,
sparse points, reprojection error, train/evaluation selection, and exact output
names in the manifest or declared reports.

For an incremental model, preserve an immutable feature-compatible base with
its image inventory, source hashes, feature dimensions/settings, matched
database, binary/text seed model, metrics, and artifact hashes. Do not
re-extract base features in an ordinary extension. Match sequential neighbors
within each new segment, deliberate cross-segment connectors, and targeted new
views to known anchors; avoid recomputing every base/base pair.

Register with normal thresholds, triangulate, retry, and relax only
still-unregistered connector frames. Persist a valid model after each bounded
attempt and stop when the registered count no longer grows. Report per-segment
coverage so a strong exterior cannot hide a weak interior.

Use `quick_check` plus the table/column, image, keypoint, descriptor,
association, and camera invariants needed by the next phase. Reserve a full
SQLite integrity scan for promotion of a new immutable base or evidence of
corruption. When reading a manifest in a shell loop, detach a child tool's stdin
with `</dev/null` so it cannot consume later records.

## Quality and delivery gates

Declare target, plausible-candidate, and experimental-only tiers before the
run. A lower tier may permit private review, but every target miss remains in
the result and blocks automatic promotion. More registered views or Gaussians
alone do not establish quality.

The runner currently records these tiers; phase scripts still produce and
evaluate the scene-specific metrics. Treat this as a workflow foundation, not
yet a general automatic QA evaluator.

Before retrieving a final candidate, require a nonempty reconstruction and
export, finite reported values, converter readability, a positive Gaussian
count, and an exact `.ply` to `.sog` to `.ply` count round-trip where supported.
Use registered camera poses near named semantic checkpoints for representative
QA. Compare baseline and candidate at transitions, interior, exterior, and weak
segments, then visually inspect for holes, floaters, smearing, and failed
alignment.

A readable 5,000-step conversion may be delivered early as a private preview
while final training continues. Label it preview/non-final; it cannot replace a
master or become publication-eligible.

## Reproducible reference, not a default

The September 5, 2026 Cabin combined model used 223 ground images plus 15
registered drone views. COLMAP 4.2.0 and Brush 0.3.0 trained 30,000 iterations
at resolution 1900, six-million-splat cap, SH degree 3, seed 42, and a 214/24
train/evaluation split. It produced 4,042,995 splats. Declare settings for each
new workflow rather than silently inheriting these values.

Publication is always separate. `publish-plan` only hashes a local `.sog` or
`.ply`; authenticated upload still requires fresh confirmation of the exact
artifact, title, and visibility.
