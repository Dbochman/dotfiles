#!/usr/bin/env python3
"""Select the sharpest JPEG in each window into a new output directory."""
import argparse
import csv
from pathlib import Path
import shutil
import statistics

import numpy as np
from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path, help="Must not already exist")
    parser.add_argument("--window", type=int, default=2)
    args = parser.parse_args()
    if args.window < 1:
        parser.error("--window must be positive")
    if args.destination.exists():
        parser.error("Destination exists; choose a new directory to preserve existing files")
    files = sorted(p for p in args.source.iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg"))
    if not files:
        parser.error("No JPEG frames found")
    scores = []
    for path in files:
        with Image.open(path) as image:
            if min(image.size) < 6:
                parser.error(f"Frame too small: {path.name}")
            image = image.convert("L").resize((image.width // 2, image.height // 2), Image.Resampling.BILINEAR)
            a = np.asarray(image, dtype=np.float32)
        lap = 4 * a[1:-1, 1:-1] - a[:-2, 1:-1] - a[2:, 1:-1] - a[1:-1, :-2] - a[1:-1, 2:]
        scores.append(float(lap.var()))
    selected = [max(range(i, min(i + args.window, len(files))), key=lambda j: scores[j])
                for i in range(0, len(files), args.window)]
    args.destination.mkdir(parents=True)
    selected_set = set(selected)
    with (args.destination / "sharpness_scores.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["raw_frame", "laplacian_var", "selected"])
        writer.writerows((p.name, f"{scores[j]:.2f}", int(j in selected_set)) for j, p in enumerate(files))
    for k, j in enumerate(selected, 1):
        shutil.copy2(files[j], args.destination / f"frame_{k:04d}.jpg")
    print(f"raw frames: {len(files)}  selected: {len(selected)}")
    print(f"sharpness min/median/max: {min(scores):.1f} / {statistics.median(scores):.1f} / {max(scores):.1f}")


if __name__ == "__main__":
    main()
