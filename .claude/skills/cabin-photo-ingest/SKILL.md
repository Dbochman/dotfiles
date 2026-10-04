---
name: cabin-photo-ingest
description: Ingest new cabin photos from the local Apple Photos library into ~/Desktop/cabin pix/, tag them against docs/photo-library.json, and sync to the shared cabin backup on dylans-mac-mini. Use when the user asks to "ingest cabin photos", "update the cabin library", or "pull new cabin pix".
---

# cabin-photo-ingest

Pull new cabin photos (Phillipston / Athol MA GPS area) from Julia's local Apple Photos into `~/Desktop/cabin pix/`, hand-tag them per the `docs/photo-library.json` schema, and sync to Dylan's mini.

## When to use

Trigger when the user says things like "ingest cabin photos", "pull new cabin pix from Photos", "update the cabin library", or similar. Default scope: everything from `2025-01-01` forward that isn't already in `~/Desktop/cabin pix/`.

## Prereqs (verify once per session)

Terminal.app needs **two** separate TCC grants:

1. **Full Disk Access** — so `photoscrawl crawl` / `import-apple` can read `~/Pictures/Photos Library.photoslibrary/database/Photos.sqlite`.
2. **Photos** — so `photoscrawl export` / `sheet` can retrieve the actual image bytes via PhotoKit. Without this, export fails with `Photos access is denied for this process`, and `sheet` returns tiles with `"source": "placeholder"` (gray thumbnails, no real pixels).

Verify both:

```bash
photoscrawl --version                      # expect 0.5.1+
photoscrawl status 2>&1 | head -2          # should show photos.sqlite path + asset counts (verifies FDA)
# Verify Photos privacy by exporting one asset:
TEST=$(sqlite3 "$HOME/Library/Application Support/photoscrawl/photos.sqlite" "SELECT id FROM asset WHERE media_type='image' LIMIT 1;")
mkdir -p /tmp/pscheck && photoscrawl export -id "$TEST" -output /tmp/pscheck -timeout 30s -json 2>&1 | head -3
```

If export reports `Photos access is denied`, fix via **System Settings → Privacy & Security → Photos → add Terminal.app** (click + and navigate to /Applications/Utilities/Terminal.app). After adding, relaunch Terminal. There's no interactive prompt for CLI processes — the user has to add Terminal manually.

FDA fix: same pane, **Full Disk Access** instead of Photos.

## Paths

| Role | Path |
|------|------|
| Apple Photos library | `~/Pictures/Photos Library.photoslibrary` |
| photoscrawl archive sqlite | `~/Library/Application Support/photoscrawl/photos.sqlite` |
| Local cabin photo folder | `~/Desktop/cabin pix/` |
| Tag manifest | `~/Repos/cabin/docs/photo-library.json` |
| Remote backup | `dylans-mac-mini:/Users/dbochman/Desktop/cabin pix/` |

## Cabin selector

Two overlapping filters identify a cabin photo:
- **Apple place label**: `Phillipston` or `Athol` (reverse-geocoded by Apple)
- **GPS bbox**: `lat ∈ [42.54, 42.66]`, `lon ∈ [-72.30, -72.08]`

Current archive counts (`photoscrawl status` to re-check):
- Phillipston (place, 2025+): ~392
- Athol (place, 2025+): ~393
- bbox (2025+): ~839 — this is the superset; use it as the primary filter.

Use the bbox query as the source of truth and treat place labels as a secondary signal when Apple's geocoding missed. Direct SQL against the archive is the simplest reliable path:

```bash
sqlite3 "$HOME/Library/Application Support/photoscrawl/photos.sqlite" <<'SQL'
.mode json
SELECT DISTINCT a.id, a.local_identifier, a.creation_date,
                l.latitude, l.longitude, a.metadata_json
FROM asset a
JOIN location_observation l ON l.asset_id = a.id
WHERE l.latitude  BETWEEN 42.54 AND 42.66
  AND l.longitude BETWEEN -72.30 AND -72.08
  AND a.creation_date >= '2025-01-01'
  AND a.deleted_at IS NULL
  AND a.media_type = 'image'
ORDER BY a.creation_date;
SQL
```

## Workflow

### 1. Refresh the archive

Only do this if the user mentions new photos they just took, or if `photoscrawl status` shows stale counts. Otherwise skip — the archive is already current from last run.

```bash
photoscrawl crawl -library "$HOME/Pictures/Photos Library.photoslibrary"
photoscrawl import-apple -library "$HOME/Pictures/Photos Library.photoslibrary" -json
```

Both are safe to re-run (atomic replace-by-source).

### 2. Build the candidate list

Query the bbox selector above and write the asset-id list to a working file. Capture creation_date + GPS so you can decide what's new vs. already synced.

```bash
WORK=$(mktemp -d /tmp/cabin-ingest.XXXX)
sqlite3 "$HOME/Library/Application Support/photoscrawl/photos.sqlite" \
  "SELECT a.id || '|' || a.local_identifier || '|' || a.creation_date || '|' || l.latitude || '|' || l.longitude
   FROM asset a JOIN location_observation l ON l.asset_id = a.id
   WHERE l.latitude BETWEEN 42.54 AND 42.66
     AND l.longitude BETWEEN -72.30 AND -72.08
     AND a.creation_date >= '2025-01-01'
     AND a.deleted_at IS NULL
     AND a.media_type = 'image'
   GROUP BY a.id
   ORDER BY a.creation_date;" > "$WORK/candidates.psv"
wc -l "$WORK/candidates.psv"
```

### 3. Dedupe against the existing folder and manifest

Already-synced photos live in `~/Desktop/cabin pix/`. Apple exports as `IMG_XXXX.jpeg` (iPhone) or `dji_fly_*_photo_optimized.jpeg` (drone — those aren't in the Photos library so they're never a dup here).

Two dedupe passes:
1. **UUID match** — files in `cabin pix/` named `<UUID>.jpeg` match by local_identifier.
2. **(date, GPS) cross-ref against manifest** — for `IMG_*` manifest entries (which outnumber UUID-named ones ~5:1), use (`YYYY-MM-DD`, lat rounded to 5 decimals, lon rounded to 5 decimals) as the uniqueness key. The archive and manifest both derive GPS from the same source, so 5-decimal rounding is a safe tight match. False positives on burst-mode shots at identical GPS are OK — those bursts arrive as adjacent `IMG_XXXX` names already present, so dropping them is correct.

```bash
python3 - <<'PY' > "$WORK/new.psv"
import json, sys
with open('/Users/juliajoy/Repos/cabin/docs/photo-library.json') as f:
    m = json.load(f)
have_uuids = {p['file'].split('.')[0].upper() for p in m['photos']
              if len(p['file']) >= 36 and p['file'][8] == '-'}
have_tuples = set()
for p in m['photos']:
    d, gps = p.get('date', ''), p.get('gps')
    if not gps or not d or not d.startswith('20'):
        continue
    have_tuples.add((d[:10], round(gps[0], 5), round(gps[1], 5)))
import os
work = os.environ['WORK']
# Also check what's on disk by UUID (manifest may lag folder)
import subprocess
disk_uuids = set()
for name in os.listdir(os.path.expanduser('~/Desktop/cabin pix/')):
    if len(name) >= 36 and name[8] == '-':
        disk_uuids.add(name.split('.')[0].upper())
have_uuids |= disk_uuids
for line in open(f'{work}/candidates.psv'):
    parts = line.strip().split('|')
    if len(parts) < 5: continue
    asset_id, local_id, created, lat_s, lon_s = parts
    if local_id.upper() in have_uuids: continue
    key = (created[:10], round(float(lat_s), 5), round(float(lon_s), 5))
    if key in have_tuples: continue
    print(line.rstrip())
PY
wc -l "$WORK/new.psv"
```

Review `$WORK/new.psv` with the user before exporting — do NOT bulk-export hundreds of photos silently. Show a summary (count, date range, GPS cluster) and let them narrow the window.

Sort by creation_date and take the newest N for a small-batch workflow:

```bash
sort -t'|' -k3 "$WORK/new.psv" | tail -N > "$WORK/batch.psv"
```

### 4. Export selected assets

`photoscrawl export` writes bytes to `<asset_hex>.bin` (always `.bin`, regardless of actual format; `-format jpeg` is a no-op flag). Loop over the batch:

```bash
mkdir -p "$WORK/export"
while IFS='|' read -r asset_id local_id created lat lon; do
  photoscrawl export -id "$asset_id" -output "$WORK/export" -timeout 2m -json \
    > "$WORK/export/$local_id.exportlog.json" 2>&1
done < "$WORK/batch.psv"
```

`photoscrawl export` is slow (library open per call, ~10–30s per asset). For large batches, consider chunks and progress output.

### 5. Post-export md5 dedupe → convert → stage

iOS Photos can hold **multiple asset records for identical bytes** (iCloud merges, shared library). In practice ~40% of a batch may collapse. Dedupe by md5 of exported bytes, pick a canonical local_identifier (lexicographically first) per md5 group, and only stage that one.

Exports come out as HEIC or TIFF-wrapped (iPhone 16 Pro ProRAW-adjacent format), not JPEG. Convert with `sips` — it handles both and preserves EXIF/GPS. Target `.jpeg` to match existing cabin pix convention (zero `.heic` files there today).

```bash
mkdir -p "$WORK/staged"
python3 - <<'PY'
import hashlib, json, os, subprocess
from pathlib import Path
work = Path(os.environ['WORK'])
rows = []
for line in (work / 'batch.psv').read_text().strip().split('\n'):
    asset_id, local_id, created, lat, lon = line.split('|')
    bin_path = work / 'export' / f"{asset_id.replace('asset:', '')}.bin"
    md5 = hashlib.md5(bin_path.read_bytes()).hexdigest()
    rows.append(dict(asset_id=asset_id, local_id=local_id, created=created,
                     lat=lat, lon=lon, bin=bin_path, md5=md5))
groups = {}
for r in rows:
    groups.setdefault(r['md5'], []).append(r)
print(f'{len(rows)} UUIDs -> {len(groups)} unique md5s')
canonical = []
for md5, members in groups.items():
    members.sort(key=lambda m: m['local_id'])
    c = members[0]
    aliases = [m['local_id'] for m in members[1:]]
    print(f"  md5 {md5[:8]} canonical={c['local_id']} aliases={aliases}")
    canonical.append(c)
for r in canonical:
    dest = work / 'staged' / f"{r['local_id']}.jpeg"
    subprocess.run(['sips', '-s', 'format', 'jpeg', str(r['bin']), '--out', str(dest)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
PY
ls "$WORK/staged/"
```

View staged files with the Read tool (yes, Claude can see jpeg) to draft captions. Then copy into the cabin folder:

```bash
cp "$WORK/staged/"*.jpeg "$HOME/Desktop/cabin pix/"
```

Confirm each copy with the user if the batch is small; batch-confirm (show file list) if large.

### 6. Hand-tag against the schema

The schema lives in `~/Repos/cabin/docs/photo-library.json`. Read `tagVocabulary` and `notes` before tagging. For each new photo, draft an entry:

```json
{
  "file": "<UUID or IMG_>.jpeg",
  "date": "YYYY-MM-DD",
  "camera": "<EXIF make/model>",
  "gps": [lat, lon],
  "location": "cabin" | "off-site",
  "tags": ["<from tagVocabulary>"],
  "caption": "<short, descriptive, newspaper-style — no editorializing>"
}
```

**Do not invent tags.** Only use values from `tagVocabulary`. The `notes` array documents the invariants — read them (season via `date` field only, `people-identifiable` is a privacy signal, `cover-candidate` is subjective, etc.).

Append entries to `photos`. Keep the array roughly chronological.

**Do not use `json.dump` to write the file** — it will reformat all `tagVocabulary` arrays onto multiple lines and `ensure_ascii`-escape em-dashes, producing a thousand-line diff. The existing file has tag arrays on a single line and uses literal `—`. Insert new entries as a textual splice before the closing `  ]\n}\n`, formatted to match the existing 4-space indent and single-line `tags` style. Use `json.dumps(..., ensure_ascii=False)` for individual field values only. Validate with `python3 -c "import json; json.load(open('docs/photo-library.json'))"` after writing.

### 7. Sync to Dylan's mini

Tar-batch the new files and rsync. `rsync` alone is fine for small deltas, but a tar pipe is faster on many small files over SSH.

```bash
# Small delta (<50 files): plain rsync
rsync -av --progress "$HOME/Desktop/cabin pix/" "dylans-mac-mini:/Users/dbochman/Desktop/cabin pix/"

# Larger delta: tar pipe (preserves mtimes; faster per-file)
cd "$HOME/Desktop/cabin pix" && \
  tar cf - <list-of-new-filenames> | ssh dylans-mac-mini "cd '/Users/dbochman/Desktop/cabin pix' && tar xf -"
```

Verify remote count after sync:

```bash
ssh dylans-mac-mini "ls '/Users/dbochman/Desktop/cabin pix' | wc -l"
```

### 8. Commit the manifest

```bash
cd ~/Repos/cabin
git add docs/photo-library.json
git commit -m "photos: ingest <N> new cabin photos from <date-range>"
# DO NOT push unless user explicitly asks
```

## Guardrails

- **Never push.** The user commits locally and pushes manually.
- **Never bulk-tag.** The caption/tag step is human-in-the-loop; propose entries one batch at a time and wait for confirmation.
- **Never invent tag values** — stick to `tagVocabulary`.
- **Dedupe twice**: (date, gps5) against the manifest before exporting (step 3), then md5 of exported bytes to collapse iOS asset-dupes (step 5). `photoscrawl export` is slow; the pre-export filter saves minutes, the post-export md5 pass catches iCloud-duplicated asset records that look distinct in the archive.
- **Terminal.app TCC** gates the whole chain. FDA covers crawl/import; **Photos privacy covers export/sheet** — they are separate panes and both are needed. If export produces "placeholder" tiles or "Photos access is denied," the Photos pane is missing Terminal.app. See the `macos-tahoe-fda-headless-binary` skill for the general TCC-for-bare-CLI pattern (though note: for PhotoKit, the responsible process is the user-facing Terminal, not the bare binary — adding Terminal is sufficient; the .app wrapper trick is not required here).

## Related

- `~/Repos/cabin/docs/photo-library.json` — the tag schema + manifest
- `macos-tahoe-fda-headless-binary` skill — if FDA breaks for the CLI
- `photoscrawl status` — authoritative archive state
