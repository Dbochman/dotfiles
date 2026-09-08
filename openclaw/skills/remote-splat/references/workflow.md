# Reconstruction, training, and publishing workflow

Load this reference before changing a job's COLMAP, Brush, conversion,
validation, or publication behavior.

## Input contract

Keep original photos immutable. Put selected source images and the exact job
script under the managed `input/` directory. Give generated COLMAP databases,
sparse/dense reconstructions, checkpoints, evaluations, and final exports
separate subdirectories so a rerun cannot overwrite source media.

Record in the job script or a staged manifest:

- source image count and any aerial/ground subsets;
- feature extractor and matcher;
- registered image and sparse-point counts plus reprojection error;
- training version, resolution, iterations, splat cap, SH degree, and seed;
- train/evaluation split and evaluation cadence;
- exact final `.ply` and `.sog` names; and
- validation commands and expected reports.

## Known-good baseline

The September 5 Cabin combined model used 223 ground images plus 15 registered
drone views. Its validated baseline was COLMAP 4.2.0 followed by Brush 0.3.0 on
the RTX 5090: 30,000 iterations, maximum resolution 1900, six-million-splat cap,
SH degree 3, seed 42, and a 214/24 train/evaluation split. It completed in about
19 minutes with 4,042,995 splats. Treat these as a reproducible reference, not
automatic settings for a different dataset.

## Completion gate

A job is not complete merely because its process exits zero. Before fetching
or proposing publication:

1. Confirm the intended COLMAP registration/reconstruction exists.
2. Confirm the final Brush export exists and is nonempty.
3. Run the job's declared converter/validator for each deliverable.
4. Confirm all reported values are finite and the export round-trips when the
   selected format supports that check.
5. Review representative views for obvious holes, floaters, failed alignment,
   or privacy-sensitive content.
6. Preserve a small manifest with versions, settings, metrics, hashes, and the
   selected artifact name.

For a seeded video extension, choose representative render poses from the
registered COLMAP frames nearest the named semantic checkpoints (for example,
interior-to-yard, yard-to-trail, midpoint, and return). This is stronger
evidence than arbitrary orbit views because it tests the exact coverage the
new footage was meant to add. Render both the baseline and candidate from each
checkpoint pose for a direct visual comparison. Compare their Gaussian counts
and coordinate spread, require finite values, and require an exact
`.ply`→`.sog`→`.ply` Gaussian-count round trip. Treat those numeric checks as a
gate, then visually inspect the fetched checkpoint renders before selecting an
artifact.

Walking video often alternates crisp and motion-blurred frames. After
registration, preserve temporal coverage by choosing the strongest frame in
each small time bucket using its triangulated COLMAP observation count; do not
blindly retain every nth frame. Keep the deliberately dense connector regions
represented so that quality filtering does not remove the transitions that
join old and new scene areas.

When the operator explicitly prioritizes maximum reconstructed coverage over a
replacement-master candidate, training may retain every registered view within
a declared resource bound. Record that selection policy and any failed
coverage checks in the training manifest, keep the current master protected,
and label the result as a candidate until the ordinary connectivity and visual
comparison gates pass. More input views or Gaussians are not by themselves
evidence of a better model.

Generated `.webp` orbit or checkpoint views may be fetched through
`remote-splat` for private visual QA. This exception does not make images or
models publishable and does not relax the SuperSplat confirmation gate.

## Scheduling and artifact handoff

Start each run with `remote-splat workflow-plan`. Its three profiles make the
operator's desired outcome explicit:

- `preview` minimizes time to the first private visual candidate;
- `best-current` spends the declared full resource budget and returns the
  strongest currently available private candidate; and
- `promotion-candidate` adds the complete comparative evidence needed before a
  separate replacement or publication decision.

All three return a converted, readable, hash-verified private artifact after
basic validation. Comparative QA can follow delivery; only promotion waits for
it. The plan intentionally does not select iteration, resolution, Gaussian-cap,
SH-degree, or seed values. Bind those values explicitly in the job manifest so
known-good historical settings remain a reference rather than an unexplained
default.

Have each long phase periodically emit a single-line `OPENCLAW_PROGRESS` JSON
marker. Use a lowercase hyphenated `phase`, integer `completed` and `total`
counters when the work has a real denominator, a short unit, and a conservative
`etaSeconds` only when recent throughput supports it. The helper reports the
latest valid marker as `progressReceipt`; absent or malformed markers remain
ordinary log text and do not fabricate an ETA. Keep markers operational and
free of source filenames, paths, credentials, and household content.

Parallelize source transfer, hashing, video review, semantic extraction,
quality scoring, and job preparation when they use independent inputs. On one
desktop, serialize large COLMAP mapper/bundle-adjustment phases and avoid
overlapping Brush with GPU feature matching; CPU, GPU, memory, and disk
contention can cost more time than parallelism saves. Prefer bounded mapper
phases that persist a valid reconstruction between expensive global solves.

Keep private artifact delivery separate from master promotion. When an
operator asks for the best available candidate, return a readable,
hash-verified `.sog` as soon as conversion succeeds and carry reconstruction or
visual-quality shortcomings as explicit advisories. Use those advisories to
block automatic replacement or publication, not access to the requested
private candidate. For quick iteration, use a deliberately named preview
profile with a small fixed QA set; reserve all-view, full-step training for a
maximum-coverage or promotion candidate.

## SuperSplat guard

`remote-splat publish-plan` is deliberately non-networking. It establishes the
local artifact name, size, and SHA-256 that an approval must bind to. Before an
authenticated upload, state the proposed title and visibility and obtain fresh
explicit confirmation. Re-hash immediately before upload and stop if it differs.

After uploading, verify the exact title, visibility, and resulting project or
share state. Do not make an artifact public merely to test it, do not overwrite
an existing project without naming it in the confirmation, and do not retry
when the first publish result is ambiguous.
