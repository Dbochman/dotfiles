---
name: video-to-gaussian-splat-macos
description: |
  End-to-end pipeline to turn a phone video into a Gaussian splat on an Apple Silicon Mac with no CUDA, and
  publish it to SuperSplat (superspl.at). Use when asked to "convert this video to a gaussian splat", "make a 3DGS
  of this scan", or to train splats on a Mac. Covers: ffmpeg frame extraction with sharpness selection, COLMAP 4.x
  on CPU, Brush (brush-cli, Metal via wgpu) with flags that actually finish on an M-series GPU, converting to
  .sog / .compressed.ply with @playcanvas/splat-transform, and the SuperSplat publishing API. Includes Brush gotchas:
  --export-path is relative to the dataset's parent dir, no progress output without RUST_LOG=info, and the
  default 1900 px / 3M-splat config collapses to <1 it/s on an M4 Pro.
author: Claude Code
version: 1.0.0
date: 2026-09-05
---

# Video to Gaussian splat on macOS (no CUDA)

## Problem
gsplat, nerfstudio, and the reference 3DGS trainer need CUDA. On a Mac the working stack is
ffmpeg -> COLMAP (CPU) -> Brush (Metal via wgpu) -> splat-transform -> SuperSplat. Each stage has a non-obvious
setting that decides whether the job takes ~1.5 hours or all night.

## Context / Trigger Conditions
- Apple Silicon Mac, Homebrew available, no NVIDIA GPU.
- Input is a single handheld video (tested: iPhone, 74 s, 2160x3840 portrait, 30 fps).
- Target output is a PLY / SOG for SuperSplat or another web viewer.

## Solution

### 0. Tooling and project directory
Check `command -v ffmpeg colmap brush-cli npx uv` before installing anything. These scripts were tested
with COLMAP 4.1.x and a September 2026 Brush checkout; inspect installed `--help` before a costly run.
Use a dedicated project directory outside the dotfiles repository. The shell scripts require its path
and refuse to overwrite existing reconstruction or training outputs.

If needed, install ffmpeg/COLMAP through Homebrew. Brush source builds require Rust; use the
[Brush build instructions](https://github.com/ArthurBrussee/brush). Do not silently install or upgrade a
machine-wide toolchain. The Python helpers require NumPy and Pillow; use an isolated environment:

```bash
PROJECT="/absolute/path/to/new-splat-project"
SCRIPTS="/absolute/path/to/dotfiles/.claude/skills/video-to-gaussian-splat-macos/scripts"
mkdir -p "$PROJECT"
uv venv "$PROJECT/.venv"
uv pip install --python "$PROJECT/.venv/bin/python" numpy pillow
```

### 1. Extract and select frames
For the tested portrait source, extract at 6 fps, downscale within 1920 pixels, then keep the sharper
of each consecutive pair using Laplacian variance. Check extracted frame orientation before reconstruction.
Start with a new `frames_raw/` directory and do not overwrite frames.

```bash
mkdir "$PROJECT/frames_raw"
ffmpeg -n -i "/absolute/path/to/video.mov" \
  -vf "fps=6,scale=1920:1920:force_original_aspect_ratio=decrease" \
  -q:v 2 "$PROJECT/frames_raw/frame_%05d.jpg"
"$PROJECT/.venv/bin/python" "$SCRIPTS/select_sharp.py" \
  "$PROJECT/frames_raw" "$PROJECT/images" --window 2
```

The selector requires a new destination directory, leaves sources intact, and writes its score CSV
inside the destination. The recorded 74-second clip yielded 446 raw frames and 223 selected frames.

### 2. COLMAP (scripts/run_colmap.sh)
```bash
bash "$SCRIPTS/run_colmap.sh" "$PROJECT"
```

Single camera, OPENCV model, `--SiftExtraction.first_octave 0` (see the colmap-cpu-matching-too-slow skill:
without it CPU matching takes hours), exhaustive matcher, mapper, then `image_undistorter` and move the `.bin`
files into `undistorted/sparse/0/` so the layout is the standard `<root>/images` + `<root>/sparse/0`.
Expect ~6 minutes for 223 frames on an M4 Pro; check `model_analyzer` for registered images and reprojection error.

### 3. Brush training (scripts/train_brush_fast.sh)
```bash
bash "$SCRIPTS/train_brush_fast.sh" "$PROJECT"
```
The script uses 20,000 iterations, a 1.5M splat cap, 1280px resolution, and stops growth at iteration
12,000. It requires fresh `colmap/fast/` and `output/` paths. It converts the exact final checkpoint,
`colmap/fast/scene_20000.ply`, rather than guessing the latest file. Conversion invokes
`npx -y @playcanvas/splat-transform`, which may download the converter.
- `--export-path` resolves relative to the dataset's PARENT directory (`colmap/fast/` here), not the cwd.
- Without `RUST_LOG=info` brush-cli prints nothing until an export. With it you get
  `Refine iter N, M splats.` every 200 iterations, which is the only way to measure the rate.
- The preview requires an explicit opacity encoding; verify the exporter convention before choosing `linear` or `logit`.
- Performance on a 24 GB M4 Pro: defaults (1920 px, 3M cap, 30k iters) ran 3.5 it/s for the first 5k, then fell
  below 0.9 it/s once splats filled up, so 30k would have taken ~8 h. The flags above held ~4 it/s at the 1.5M cap
  and finished 20k iterations in 73 minutes with a clean result. Splat count hit the cap by iteration ~6000.

### 4. Convert and check
The training script already converts the final checkpoint. Equivalent manual commands:
```bash
npx -y @playcanvas/splat-transform -w "$PROJECT/colmap/fast/scene_20000.ply" -N "$PROJECT/output/scene.sog"          # 22 MB for 1.5M splats
npx -y @playcanvas/splat-transform -w "$PROJECT/colmap/fast/scene_20000.ply" -N "$PROJECT/output/scene.compressed.ply"  # 88 MB
```
`--stats` prints per-column stats (check nans = 0). For a visual sanity check without a browser use
scripts/point_preview.py: it projects splat centres through the real COLMAP camera poses on the CPU and writes
PNGs you can compare with the source frames. Do not use splat-transform's `.webp` GPU render while Brush is
training; it competed for the GPU and hung for 10+ minutes.

Create a text camera model for the preview, then pass the PLY path explicitly:
```bash
mkdir "$PROJECT/colmap/preview-model"
colmap model_converter --input_path "$PROJECT/colmap/undistorted/sparse/0" \
  --output_path "$PROJECT/colmap/preview-model" --output_type TXT
"$PROJECT/.venv/bin/python" "$SCRIPTS/point_preview.py" \
  "$PROJECT/colmap/preview-model" "frame_0001.jpg" "$PROJECT/preview" \
  "$PROJECT/colmap/fast/scene_20000.ply" --opacity-format linear
```
This helper supports vertex-only binary little-endian splat PLYs and one undistorted PINHOLE camera.
It renders point centres, not full Gaussian appearance; select the opacity format to match the exporter.

### 5. Publish (scripts/publish_to_supersplat.py)
Publishing is optional. Run only when the user explicitly requests an upload; creating local artifacts
is not authorization to publish. Supply the token through the environment without printing it.

SuperSplat publishing API (`https://playcanvas.com/api/supersplat/v1`): POST `/splats/uploads` with
`sourceFormat` (`ply` or `sog`), `contentLength`, `title`; POST `/splats/uploads/{id}/part-upload-urls`; PUT each part
to the signed URL and keep the ETag; POST `/splats/uploads/{id}/complete`. Bearer auth with a PlayCanvas access
token. Scenes are created unlisted. The script reads the token from `SUPERSPLAT_TOKEN`; never handle the token
in chat.

## Verification
Run the offline regression suite (no training or uploads):
```bash
"$PROJECT/.venv/bin/python" -m unittest discover -s "$SCRIPTS/../tests" -v
```

- COLMAP: all frames registered in one model, reprojection error < 1 px.
- Brush: `Refine iter` lines advancing; exports appear in `<dataset parent>/<export-path>/`.
- point_preview.py renders match the source frames (cabin walls, roof, tree line in the right places).
- splat-transform `--stats`: expected gaussian count, `nans 0`.

## Example
Cabin project, 2026-09-04: 74 s iPhone clip -> 223 frames -> COLMAP 5m46s -> Brush 73 min -> cabin.sog 22 MB.
Total wall time about 1.5 h once the tooling was in place.

## Notes
- Preserve the `undistorted/` dataset so training can be repeated without redoing SfM.
- The Claude in-app browser could not open `file://` or a localhost preview server in this session; rely on the
  CPU projection or hand the .sog to the user to drop into https://superspl.at/editor.
- Scripts refuse existing output paths; preserve prior runs and choose a fresh project for reruns.

## References
- [Brush repository](https://github.com/ArthurBrussee/brush) and [releases](https://github.com/ArthurBrussee/brush/releases)
- [splat-transform](https://github.com/playcanvas/splat-transform)
- [SuperSplat API reference](https://developer.playcanvas.com/user-manual/api/supersplat/)
- [SuperSplat publishing API announcement](https://blog.playcanvas.com/new-in-supersplat-introducing-the-new-publishing-api/)
- [COLMAP FAQ](https://colmap.github.io/faq.html)
- [Gaussian splatting on Mac overview (RadianceKit, 2026)](https://www.radiancekit.de/gaussian-splatting-mac/)
