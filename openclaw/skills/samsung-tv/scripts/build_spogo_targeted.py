#!/usr/bin/env python3
"""Build the isolated Frame Spotify client; never replace Homebrew spogo."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

COMMIT = "980cf0c3137518423638c8b0a4ca6d3ea5cf962c"
VERSION = "0.10.3-frame.1"


def main():
    patch = Path(__file__).resolve().parent.parent / "patches/spogo-0.10.3-explicit-device.patch"
    patch_hash = hashlib.sha256(patch.read_bytes()).hexdigest()
    parent = Path.home() / ".local/share/samsung-tv-tools"
    target = parent / ("spogo-" + VERSION)
    if target.exists():
        receipt = json.loads((target / "build.json").read_text())
        if (receipt.get("source_commit") == COMMIT and receipt.get("patch_sha256") == patch_hash
                and receipt.get("binary_sha256") == hashlib.sha256((target / "spogo").read_bytes()).hexdigest()):
            print("Verified existing isolated build:", target)
            return
        raise RuntimeError("Existing build differs; inspect it before installing a new version")
    env = dict(os.environ, GOTOOLCHAIN="local")
    go_version = subprocess.check_output(["go", "version"], env=env, text=True).strip()
    match = re.search(r"go(\d+)\.(\d+)\.(\d+)", go_version)
    if not match or tuple(map(int, match.groups())) < (1, 27, 3):
        raise RuntimeError("Use the reviewed Go 1.27.3 or newer local compiler; no automatic toolchain download")
    with tempfile.TemporaryDirectory(prefix="frame-spogo-build-") as temp:
        source = Path(temp) / "source"
        subprocess.run(["git", "clone", "--no-checkout", "--filter=blob:none",
                        "https://github.com/openclaw/spogo.git", str(source)], check=True)
        subprocess.run(["git", "checkout", "--detach", COMMIT], cwd=source, check=True)
        subprocess.run(["git", "apply", "--check", str(patch)], cwd=source, check=True)
        subprocess.run(["git", "apply", str(patch)], cwd=source, check=True)
        subprocess.run(["go", "test", "./internal/spotify", "./internal/app"], cwd=source, env=env, check=True)
        binary = Path(temp) / "spogo"
        subprocess.run(["go", "build", "-trimpath", "-ldflags",
                        "-X github.com/steipete/spogo/internal/cli.version=" + VERSION,
                        "-o", str(binary), "./cmd/spogo"], cwd=source, env=env, check=True)
        receipt = {"version": VERSION, "source_commit": COMMIT, "patch_sha256": patch_hash,
                   "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "compiler": go_version}
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".spogo-install-", dir=parent) as staging:
            ready = Path(staging) / "ready"
            ready.mkdir(mode=0o700)
            shutil.copy2(binary, ready / "spogo")
            (ready / "build.json").write_text(json.dumps(receipt, indent=2) + "\n")
            ready.rename(target)
    print("Installed verified isolated build:", target)


if __name__ == "__main__":
    main()
