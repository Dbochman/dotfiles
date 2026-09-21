#!/usr/bin/env bash
# Faster Brush configuration for Apple Silicon: 1280px, 1.5M splat cap, 20k iters.
set -euo pipefail
if [[ $# -ne 1 ]]; then
  echo "Usage: $0 PROJECT_DIRECTORY" >&2
  exit 2
fi
cd "$1"
export PATH="$HOME/.cargo/bin:$PATH"
export RUST_LOG=${RUST_LOG:-info}
DATASET=colmap/undistorted
EXPORT_DIR=colmap/fast            # Brush resolves --export-path relative to the dataset's parent dir
[[ -d "$DATASET/images" && -d "$DATASET/sparse/0" ]] || { echo "Missing undistorted dataset" >&2; exit 1; }
[[ ! -e "$EXPORT_DIR" && ! -e output ]] || { echo "Training/export output exists; preserve it and use a fresh project" >&2; exit 1; }
for tool in brush-cli npx; do command -v "$tool" >/dev/null; done
mkdir -p "$EXPORT_DIR" output

echo "=== fast training start $(date '+%H:%M:%S') ==="
brush-cli "$DATASET" \
  --total-train-iters 20000 \
  --growth-stop-iter 12000 \
  --max-splats 1500000 \
  --max-resolution 1280 \
  --export-every 2500 \
  --export-path fast \
  --export-name "scene_{iter}.ply" \
  --eval-every 1000000 \
  --seed 42
echo "=== training finished $(date '+%H:%M:%S') ==="

FINAL="$EXPORT_DIR/scene_20000.ply"
[[ -s "$FINAL" ]] || { echo "Final checkpoint missing" >&2; exit 1; }
echo "=== final export: $FINAL ($(du -h "$FINAL" | cut -f1)) ==="
echo "=== converting for SuperSplat ==="
npx -y @playcanvas/splat-transform -w "$FINAL" -N output/scene.compressed.ply
npx -y @playcanvas/splat-transform -w "$FINAL" -N --stats output/scene.sog | grep -E "gaussians|opacity|nans"
ls -lh output/
echo "=== DONE $(date '+%H:%M:%S') ==="
