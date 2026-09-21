#!/usr/bin/env python3
"""Render splat centres for pose checks; this is not a Gaussian renderer."""
import argparse
import glob
import os
from pathlib import Path
import numpy as np
from PIL import Image


def read_ply(path):
    """Read vertex-only binary little-endian splat PLYs; reject other layouts."""
    with open(path, "rb") as f:
        header = []
        for _ in range(1024):
            raw = f.readline(4096)
            if not raw or not raw.endswith(b"\n"):
                raise ValueError("Truncated or oversized PLY header")
            line = raw.decode("ascii").strip()
            header.append(line)
            if line == "end_header":
                break
        else:
            raise ValueError("PLY header exceeds 1024 lines")
        if header[0] != "ply" or "format binary_little_endian 1.0" not in header:
            raise ValueError("Expected binary little-endian PLY")
        elements = [line.split() for line in header if line.startswith("element ")]
        if len(elements) != 1 or elements[0][1] != "vertex":
            raise ValueError("Expected a vertex-only splat PLY")
        n = int(elements[0][2])
        if n <= 0:
            raise ValueError("PLY has no vertices")
        props = [line.split() for line in header if line.startswith("property ")]
        tmap = {"float": "<f4", "float32": "<f4", "double": "<f8", "uchar": "u1", "uint8": "u1", "int": "<i4", "uint": "<u4"}
        if any(len(prop) != 3 or prop[1] not in tmap for prop in props):
            raise ValueError("Unsupported PLY property")
        dtype = np.dtype([(prop[2], tmap[prop[1]]) for prop in props])
        required = {"x", "y", "z", "opacity", "f_dc_0", "f_dc_1", "f_dc_2"}
        if not required.issubset(dtype.names or ()):
            raise ValueError("PLY is missing splat properties")
        if os.fstat(f.fileno()).st_size - f.tell() != n * dtype.itemsize:
            raise ValueError("PLY payload size does not match header")
        return np.fromfile(f, dtype=dtype, count=n)


def q2R(qw,qx,qy,qz):
    return np.array([[1-2*(qy*qy+qz*qz), 2*(qx*qy-qz*qw), 2*(qx*qz+qy*qw)],
                     [2*(qx*qy+qz*qw), 1-2*(qx*qx+qz*qz), 2*(qy*qz-qx*qw)],
                     [2*(qx*qz-qy*qw), 2*(qy*qz+qx*qw), 1-2*(qx*qx+qy*qy)]])


def main():
    parser = argparse.ArgumentParser(description="CPU point preview of a single-camera undistorted COLMAP model")
    parser.add_argument("model", help="COLMAP text model directory")
    parser.add_argument("frames", help="Comma-separated image names")
    parser.add_argument("out_dir")
    parser.add_argument("pattern", help="PLY filename or quoted glob")
    parser.add_argument("--opacity-format", choices=("linear", "logit"), required=True)
    args = parser.parse_args()
    model, frames, out_dir = args.model, args.frames.split(","), args.out_dir
    pattern, opacity_format = args.pattern, args.opacity_format
    matches = sorted(glob.glob(pattern), key=os.path.getmtime)
    if not matches:
        raise ValueError("No PLY files match the supplied pattern")
    ply = matches[-1]
    print("checkpoint:", ply, f"{os.path.getsize(ply)/1e6:.0f} MB")

    d = read_ply(ply)
    P = np.stack([d["x"], d["y"], d["z"]], 1).astype(np.float64)
    op = d["opacity"].astype(np.float64)
    if opacity_format == "logit":
        op = 1 / (1 + np.exp(-np.clip(op, -700, 700)))
    rgb = np.clip(0.5 + 0.2820948 * np.stack([d["f_dc_0"], d["f_dc_1"], d["f_dc_2"]], 1), 0, 1)
    keep = op > 0.3
    P, rgb = P[keep], rgb[keep]
    print(f"splats: {len(d)}  kept (opacity>0.3): {keep.sum()}")

    cams = {}
    lines = [l for l in (Path(model) / "images.txt").read_text().splitlines() if not l.startswith("#")]
    for l in lines[0::2]:
        p = l.split()
        q = list(map(float, p[1:5]))
        t = np.array(list(map(float, p[5:8])))
        cams[p[9]] = (q2R(*q), t)
    camera_rows = [line.split() for line in (Path(model) / "cameras.txt").read_text().splitlines() if line.strip() and not line.startswith("#")]
    if len(camera_rows) != 1 or camera_rows[0][1] != "PINHOLE":
        raise ValueError("Preview requires one undistorted PINHOLE camera")
    cam = camera_rows[0]
    W, H, fx, fy, cx, cy = map(float, cam[2:8])
    s = min(1.0, 960 / max(W, H))
    ow, oh = max(1, round(W * s)), max(1, round(H * s))
    os.makedirs(out_dir, exist_ok=True)

    for name in frames:
        R, t = cams[name]
        for tag, F in (("noflip", np.array([1.,1.,1.])), ("flip", np.array([1.,-1.,-1.]))):
            # world->camera for COLMAP: Xc = R X + t. Apply frame flip F to splat positions first.
            Xc = (P * F) @ R.T + t
            z = Xc[:, 2]
            front = z > 0.05
            u = fx*s * Xc[:, 0]/np.where(front, z, 1) + cx*s
            v = fy*s * Xc[:, 1]/np.where(front, z, 1) + cy*s
            inb = front & (u >= 0) & (u < ow) & (v >= 0) & (v < oh)
            img = np.zeros((oh, ow, 3), np.uint8)
            idx = np.where(inb)[0]
            idx = idx[np.argsort(-z[idx])]      # far to near
            img[v[idx].astype(int), u[idx].astype(int)] = (rgb[idx]*255).astype(np.uint8)
            outp = f"{out_dir}/points_{os.path.splitext(os.path.basename(name))[0]}_{tag}.png"
            Image.fromarray(img).save(outp)
            print(f"{name} {tag:6s}: in-frame {inb.mean()*100:5.1f}%  median depth {np.median(z[inb]) if inb.any() else float('nan'):.2f} -> {outp}")


if __name__ == "__main__":
    main()
