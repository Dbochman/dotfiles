#!/usr/bin/env python3
"""Publish a Gaussian splat (.ply / .compressed.ply / .sog) to superspl.at via the SuperSplat Publishing API.

Usage:
  export SUPERSPLAT_TOKEN="<your PlayCanvas access token>"   # create at playcanvas.com -> Account -> API Tokens
  python3 publish_to_supersplat.py output/cabin.sog --title "Cabin" --description "iPhone video -> COLMAP -> Brush"

The scene is created *unlisted*; make it public from the returned edit URL if desired.
Uses only the Python standard library.
"""
import argparse, json, os, sys, uuid, urllib.request, urllib.error

API = "https://playcanvas.com/api/supersplat/v1"

def call(method, url, token=None, body=None, raw=None, extra_headers=None):
    headers = dict(extra_headers or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    if raw is not None:
        data = raw
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            payload = r.read()
            return r.status, dict(r.headers), (json.loads(payload) if payload and r.headers.get("Content-Type", "").startswith("application/json") else payload)
    except urllib.error.HTTPError as e:
        # Signed URLs and response bodies can contain credentials.
        sys.exit(f"HTTP {e.code} during {method}; URL and response body withheld")
    except (urllib.error.URLError, TimeoutError):
        sys.exit(f"Network failure during {method}; connection details withheld")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--title", required=True)
    ap.add_argument("--description", default="")
    args = ap.parse_args()

    token = os.environ.get("SUPERSPLAT_TOKEN")
    if not token:
        sys.exit("Set SUPERSPLAT_TOKEN in your environment first (never paste it into chat).")

    path = args.file
    size = os.path.getsize(path)
    if size == 0:
        sys.exit("Input file is empty")
    name = os.path.basename(path).lower()
    if name.endswith(".sog"):
        fmt = "sog"
    elif name.endswith(".ply"):          # includes .compressed.ply
        fmt = "ply"
    else:
        sys.exit("Unsupported extension; use .ply, .compressed.ply, or .sog")

    print(f"[1/4] creating upload session ({fmt}, {size/1e6:.1f} MB)")
    _, _, sess = call("POST", f"{API}/splats/uploads", token, {
        "sourceFormat": fmt,
        "contentLength": size,
        "title": args.title,
        "description": args.description,
        "uploadClient": {"name": "video-splat-cli", "version": "1.0"},
    }, extra_headers={"Idempotency-Key": str(uuid.uuid4())})
    upload_id, part_size = sess["id"], int(sess["partSize"])
    n_parts = max(1, -(-size // part_size))
    print(f"      part size {part_size/1e6:.0f} MB, {n_parts} part(s)")

    print(f"[2/4] requesting signed URLs")
    _, _, urls = call("POST", f"{API}/splats/uploads/{upload_id}/part-upload-urls", token,
                      {"parts": list(range(1, n_parts + 1))})
    url_by_part = {u["partNumber"]: u["url"] for u in urls["urls"]}

    print(f"[3/4] uploading parts")
    etags = []
    with open(path, "rb") as fh:
        for p in range(1, n_parts + 1):
            chunk = fh.read(part_size)
            _, hdrs, _ = call("PUT", url_by_part[p], raw=chunk,
                              extra_headers={"Content-Type": "application/octet-stream"})
            etag = hdrs.get("ETag") or hdrs.get("Etag")
            if not etag:
                sys.exit(f"no ETag returned for part {p}")
            etags.append({"partNumber": p, "etag": etag})
            print(f"      part {p}/{n_parts} ok")

    print(f"[4/4] completing upload")
    _, _, done = call("POST", f"{API}/splats/uploads/{upload_id}/complete", token, {"parts": etags})
    sid = done.get("splatId")
    print("\nPublished (unlisted).")
    print(f"  view: https://superspl.at/scene/{sid}")
    print(f"  edit: {done.get('editUrl')}")

if __name__ == "__main__":
    main()
