#!/usr/bin/env bash
# COLMAP structure-from-motion for the cabin frames (CPU only on macOS).
set -euo pipefail
if [[ $# -ne 1 ]]; then
  echo "Usage: $0 PROJECT_DIRECTORY" >&2
  exit 2
fi
cd "$1"
[[ -d images ]] || { echo "Missing images directory" >&2; exit 1; }
[[ ! -e colmap ]] || { echo "colmap already exists; choose a fresh project directory" >&2; exit 1; }
for tool in colmap sqlite3; do command -v "$tool" >/dev/null; done
mkdir colmap
DB=colmap/database.db
export OMP_NUM_THREADS=$(sysctl -n hw.logicalcpu)

echo "=== [1/4] feature extraction ($(ls images | wc -l | tr -d ' ') images) ==="
colmap feature_extractor \
  --database_path "$DB" --image_path images \
  --ImageReader.single_camera 1 --ImageReader.camera_model OPENCV \
  --FeatureExtraction.use_gpu 0 --FeatureExtraction.max_image_size 1920 \
  --SiftExtraction.first_octave 0
echo "features per image (sample): $(sqlite3 "$DB" 'select group_concat(rows) from (select rows from keypoints limit 5)')"

echo "=== [2/4] exhaustive matching ==="
colmap exhaustive_matcher --database_path "$DB" --FeatureMatching.use_gpu 0
echo "verified pairs: $(sqlite3 "$DB" 'select count(*) from two_view_geometries where rows>0')"

echo "=== [3/4] incremental mapping ==="
mkdir -p colmap/sparse
colmap mapper --database_path "$DB" --image_path images --output_path colmap/sparse

echo "--- models produced ---"
BEST=""; BESTN=0
for m in colmap/sparse/*/; do
  n=$(colmap model_analyzer --path "$m" 2>&1 | grep -oE "Registered images:[[:space:]]+[0-9]+" | grep -oE "[0-9]+$" || echo 0)
  echo "model $m: $n registered images"
  colmap model_analyzer --path "$m" 2>&1 | grep -E "Points|Mean reprojection|Mean observations per image|Mean track length" || true
  if (( n > BESTN )); then BEST=$m; BESTN=$n; fi
done
[[ -n "$BEST" ]] || { echo "No registered model; inspect reconstruction output" >&2; exit 1; }
echo "best model: $BEST ($BESTN images)"

echo "=== [4/4] undistort to PINHOLE for the splat trainer ==="
colmap image_undistorter --image_path images --input_path "$BEST" \
  --output_path colmap/undistorted --output_type COLMAP
mkdir -p colmap/undistorted/sparse/0
mv colmap/undistorted/sparse/*.bin colmap/undistorted/sparse/0/
echo "=== DONE ==="; ls colmap/undistorted/sparse/0; ls colmap/undistorted/images | wc -l
