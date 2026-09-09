#!/usr/bin/env bash
# Lightweight write-only-within-job canary for the WSL and Windows splat toolchain.
set -euo pipefail

umask 077

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
job_root=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
output_root="$job_root/outputs/preflight-v1"
images="$output_root/images"
database="$output_root/canary.db"
report="$output_root/preflight-report.json"
gpu_probe=/usr/lib/wsl/lib/nvidia-smi
tool_root=/mnt/c/Users/Owner/Documents/Codex/2026-09-05/c/work
colmap="$tool_root/colmap/bin/colmap.exe"
brush="$tool_root/brush/brush_app.exe"
transform_cli="$tool_root/conversion/node_modules/@playcanvas/splat-transform/bin/cli.mjs"

if [[ -e "$output_root" ]]; then
  echo "preflight output already exists" >&2
  exit 2
fi
for dependency in "$gpu_probe" "$colmap" "$brush" "$transform_cli"; do
  if [[ ! -f "$dependency" ]]; then
    echo "required splat toolchain component is unavailable" >&2
    exit 2
  fi
done

node=""
for candidate in \
  "$tool_root/runtime/node.exe" \
  "$tool_root/node/node.exe" \
  "$script_dir/runtime/node.exe" \
  "$script_dir/node.exe"; do
  if [[ -f "$candidate" ]]; then
    node="$candidate"
    break
  fi
done
if [[ -z "$node" ]]; then
  echo "Windows Node runtime for splat-transform is unavailable" >&2
  exit 2
fi

gpu_name=$($gpu_probe --query-gpu=name --format=csv,noheader | head -n 1)
case "$gpu_name" in
  *"RTX 5090"*) ;;
  *) echo "expected RTX 5090 is unavailable" >&2; exit 2 ;;
esac

mkdir -p "$images"
ffmpeg -hide_banner -loglevel error -f lavfi \
  -i 'testsrc2=size=640x480:rate=1' -frames:v 1 "$images/canary-01.png"
ffmpeg -hide_banner -loglevel error -f lavfi \
  -i 'testsrc2=size=640x480:rate=1' -vf 'rotate=0.01:fillcolor=black' \
  -frames:v 1 "$images/canary-02.png"

database_win=$(wslpath -w "$database")
images_win=$(wslpath -w "$images")
transform_cli_win=$(wslpath -w "$transform_cli")

echo 'OPENCLAW_PROGRESS {"phase":"toolchain-preflight","completed":0,"total":3,"unit":"checks"}'
"$colmap" feature_extractor \
  --database_path "$database_win" \
  --image_path "$images_win" \
  --ImageReader.single_camera 1 \
  --ImageReader.camera_model PINHOLE \
  --FeatureExtraction.type SIFT \
  --FeatureExtraction.use_gpu 1
echo 'OPENCLAW_PROGRESS {"phase":"toolchain-preflight","completed":1,"total":3,"unit":"checks"}'

"$colmap" exhaustive_matcher \
  --database_path "$database_win" \
  --FeatureMatching.type SIFT_BRUTEFORCE \
  --FeatureMatching.use_gpu 1
echo 'OPENCLAW_PROGRESS {"phase":"toolchain-preflight","completed":2,"total":3,"unit":"checks"}'

if ! brush_help=$(timeout 20s "$brush" --help 2>&1 | tr -d '\r'); then
  echo "Brush runtime/DLL canary failed" >&2
  exit 2
fi
if [[ -z "$brush_help" ]]; then
  echo "Brush runtime/DLL canary failed" >&2
  exit 2
fi
node_version=$("$node" --version | tr -d '\r')
transform_version=$("$node" "$transform_cli_win" -v 2>&1 | tr -d '\r' | tail -n 1)

python3 - "$database" "$report" "$node_version" "$transform_version" <<'PY'
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys


database = Path(sys.argv[1])
report = Path(sys.argv[2])
node_version = sys.argv[3]
transform_version = sys.argv[4]
required_columns = {
    "cameras": {"camera_id", "model", "width", "height", "params"},
    "images": {"image_id", "name", "camera_id"},
    "keypoints": {"image_id", "rows", "cols", "data"},
    "descriptors": {"image_id", "rows", "cols", "data"},
    "matches": {"pair_id", "rows", "cols", "data"},
    "two_view_geometries": {"pair_id", "rows", "cols", "data", "config"},
}
with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
    quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
    schema = {
        table: {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for table in required_columns
    }
    counts = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in required_columns
    }
    descriptor_dimensions = {
        row[0] for row in connection.execute("SELECT DISTINCT cols FROM descriptors")
    }
schema_ok = all(columns.issubset(schema[table]) for table, columns in required_columns.items())
payload = {
    "ok": (
        quick_check == "ok"
        and schema_ok
        and counts["images"] == 2
        and counts["keypoints"] == 2
        and counts["descriptors"] == 2
        and counts["matches"] >= 1
        and descriptor_dimensions == {128}
        and node_version.startswith("v")
        and transform_version.startswith("splat-transform v")
    ),
    "databaseQuickCheck": quick_check,
    "schemaCompatible": schema_ok,
    "imageCount": counts["images"],
    "matchedPairCount": counts["matches"],
    "descriptorDimensions": sorted(descriptor_dimensions),
    "matcherFeatureType": "SIFT_BRUTEFORCE",
    "nodeVersion": node_version,
    "transformVersion": transform_version,
}
report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
if not payload["ok"]:
    raise SystemExit("splat toolchain preflight failed")
PY

echo 'OPENCLAW_PROGRESS {"phase":"toolchain-preflight","completed":3,"total":3,"unit":"checks"}'
