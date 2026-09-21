---
name: colmap-cpu-matching-too-slow
description: |
  Fix COLMAP 4.x feature matching that crawls on CPU (macOS / no CUDA): exhaustive_matcher stuck on
  "Processing block [1/5, 1/5]" for 15+ minutes, ~1 image pair per second, colmap process using 10+ GB RAM,
  and the log showing 14-16k "Features:" per frame even though --SiftExtraction.max_num_features 8192 was set.
  Use when: (1) COLMAP matching ETA is hours for a few hundred images, (2) max_num_features seems ignored,
  (3) old flags like --SiftExtraction.use_gpu / --SiftMatching.use_gpu are rejected in COLMAP 4.x.
  Root cause: max_num_features is not a hard cap and CPU brute-force matching scales with features squared.
  Fix: --SiftExtraction.first_octave 0 (plus renamed use_gpu flags); measure progress with sqlite3 on database.db.
author: Claude Code
version: 1.0.0
date: 2026-09-05
---

# COLMAP CPU matching too slow (features not capped)

## Problem
On a CUDA-less machine (Homebrew COLMAP on macOS), `exhaustive_matcher` looked alive (700-900% CPU) but
committed only ~1,100 verified pairs in 18 minutes for 223 frames (24,753 pairs), an ETA near 7 hours, while
holding 11 GB of RAM. Feature extraction had produced 14-16k SIFT features per 1080x1920 frame despite
`--SiftExtraction.max_num_features 8192`. CPU descriptor matching is O(features^2) per pair, and each matcher
thread allocates a features x features float distance matrix (16k^2 x 4 B = 1 GB per thread), which explains
both the time and the memory.

## Context / Trigger Conditions
- COLMAP 4.1.x (`colmap -h` says "without CUDA"), `--FeatureMatching.use_gpu 0`.
- Log stays on `pairing.cc ... Processing block [1/N, 1/N]` for many minutes.
- `sqlite3 colmap-retuned/database.db "select rows from keypoints limit 5"` shows >10k per image.
- Flags `--SiftExtraction.use_gpu`, `--SiftMatching.use_gpu`, `--SiftMatching.guided_matching` are not recognized
  (renamed to `--FeatureExtraction.use_gpu`, `--FeatureMatching.use_gpu`, `--FeatureMatching.guided_matching`).

## Solution
1. Inspect the active job before stopping it. Preserve its database and outputs. For a rerun, create a
   fresh output directory (`mkdir colmap-retuned`) and use `colmap-retuned/database.db` in every command below.
   The examples assume `images/` contains the input frames; do not reuse an existing database.
2. Re-extract with the first octave at full resolution instead of the default upsampled octave (-1):
   ```bash
   colmap feature_extractor --database_path colmap-retuned/database.db --image_path images \
     --ImageReader.single_camera 1 --ImageReader.camera_model OPENCV \
     --FeatureExtraction.use_gpu 0 --FeatureExtraction.max_image_size 1920 \
     --SiftExtraction.first_octave 0
   ```
   Measured on one 1080x1920 frame: default 14-16k features; `max_num_features 6000` alone still gave 10.2k;
   `first_octave 0` gave 3.8k (about 5.8k on richer frames); `first_octave 0` + `max_image_size 1280` gave 1.9k
   (too few for detail). ~4-6k per frame is plenty for a textured outdoor scene.
3. Match without guided matching (it roughly doubles the work):
   ```bash
   colmap exhaustive_matcher --database_path colmap-retuned/database.db --FeatureMatching.use_gpu 0
   ```
4. Watch real progress instead of the log (blocks are only logged when they start):
   ```bash
   sqlite3 colmap-retuned/database.db "select count(*) from two_view_geometries;"
   ```

## Verification
After the change, 223 frames extracted in 26 s, matched ~90 pairs/s (all 24,753 pairs in ~3 min), the colmap
process used <500 MB, and the full pipeline (extract, exhaustive match, mapper, undistort) took 5m46s.
In this recorded run, reconstruction metrics were: 223/223 registered, 42.6k points, 0.69 px mean reprojection error.

## Example
Symptom seen in this session (per-frame log line): `Features: 14143 (SIFT)` with `max_num_features 8192` set.
Query that exposed it: `sqlite3 colmap-retuned/database.db "select rows from keypoints limit 3"` -> 16191, 16288, 14823.

## Notes
- For long video walkarounds, `sequential_matcher` with `--SequentialMatching.overlap 15-20` is a way to reduce the number of candidate pairs, but it loses loop closure unless a vocab tree is supplied; exhaustive matching on ~5k features was cheap
  enough that it was not needed.
- The COLMAP FAQ itself recommends `first_octave 0` and a smaller `max_image_size` for CPU extraction RAM.
- On Apple Silicon the OpenGL SiftGPU path is unreliable headless; keep `use_gpu 0` for both stages.

## References
- [COLMAP FAQ: CPU feature extraction and RAM](https://colmap.github.io/faq.html)
- [COLMAP feature extraction and matching docs](https://colmap.github.io/features.html)
- [colmap-parameters reference (first_octave semantics)](https://github.com/mwtarnowski/colmap-parameters)
