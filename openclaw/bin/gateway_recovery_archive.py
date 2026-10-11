from pathlib import Path


def sqlite_files_from_manifest(restored, manifest):
    candidates = []
    for relative, entry in manifest.items():
        if entry["type"] != "file" or entry["size"] < 16:
            continue
        path = Path(restored) / relative
        with path.open("rb") as stream:
            if stream.read(16) == b"SQLite format 3\x00":
                candidates.append((relative, path))
    return candidates
