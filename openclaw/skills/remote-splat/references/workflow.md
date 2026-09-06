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

## SuperSplat guard

`remote-splat publish-plan` is deliberately non-networking. It establishes the
local artifact name, size, and SHA-256 that an approval must bind to. Before an
authenticated upload, state the proposed title and visibility and obtain fresh
explicit confirmation. Re-hash immediately before upload and stop if it differs.

After uploading, verify the exact title, visibility, and resulting project or
share state. Do not make an artifact public merely to test it, do not overwrite
an existing project without naming it in the confirmation, and do not retry
when the first publish result is ambiguous.
