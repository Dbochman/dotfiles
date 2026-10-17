# Cabin Photo Catalog

**Schedule:** Daily at approximately 10:15 AM local time, after the 9:15 AM
iCloud Shared Album ingest

**Service:** `ai.openclaw.cabin-photo-catalog`

The Cabin photo catalog reconciles the downloaded Shared Album with the
existing human-curated Cabin photo manifest and prepares a private, bounded
catalog for the magazine producer. Originals remain untouched in the iCloud
inbox. The service creates owner-only JPEG review derivatives and sends only
new, unreconciled post-cutoff derivatives through OpenClaw's stateless image
description capability.

## Safety and editorial boundary

- A decisive mutual visual match inherits the existing human-curated record;
  the model does not rewrite its caption or tags.
- A mutual near-pixel-identical match with more than one equally close legacy
  record is retained as a `duplicate_cluster`; every curated record remains in
  the producer catalog, while the Shared Album copy is not exported twice.
- A probable or ambiguous historical match remains `review_match` and is not
  exported as a second producer photo.
- An older unmatched image remains `review_unmatched`; it is never silently
  treated as new.
- Only images captured after the configured cutoff enter automatic tagging.
- Low-confidence and invalid model results remain in review or bounded retry
  state. Model attempts stop after three failures unless an attended run uses
  `--retag-low-confidence`.
- The model must return strict JSON and can use only the manifest's controlled
  vocabulary. Privacy and medium tags are derived from explicit flags and file
  metadata. A model cover signal remains a suggestion; only human-curated
  records may carry the authoritative `cover-candidate` tag.
- Videos are retained in the original inbox but excluded from the photo
  producer catalog.
- No background run commits, pushes, publishes, or changes an issue. The
  magazine's existing approval gate remains authoritative.

Visible text or symbols in a photo are treated as untrusted scene content.
The prompt prohibits following instructions found in an image and prohibits
identifying people or pets by name.

## Runtime files

| File | Path |
|------|------|
| Script source | `openclaw/bin/cabin-photo-catalog.py` |
| Deployed script | `~/.openclaw/bin/cabin-photo-catalog.py` |
| Config | `~/.openclaw/cabin-photo-catalog/config.json` |
| Private reconciliation state | `~/.openclaw/cabin-photo-catalog/catalog.json` |
| Owner-only derivatives | `~/.openclaw/cabin-photo-catalog/previews/` |
| Safe operational status | `~/.openclaw/cabin-photo-catalog/status.json` |
| Producer catalog | `~/Repos/cabin/data/photo-catalog/catalog.json` |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.cabin-photo-catalog.plist` |
| Logs | `~/.openclaw/logs/cabin-photo-catalog.{log,err.log}` |

The config, state, status, producer catalog, and derivatives are mode `0600`
inside owner-only directories. Logs contain only hashed asset IDs, confidence
labels, failure categories, and aggregate counts—never captions, tags, GPS,
model text, original filenames, or image contents.

## Configuration

The owner-only config is explicit because the Shared Album archive and Cabin
repository are separate systems:

```json
{
  "inboxDir": "~/Pictures/openclaw-icloud-inbox/cabin",
  "legacyManifest": "~/Repos/cabin/docs/photo-library.json",
  "legacyPhotoDir": "~/Desktop/cabin pix",
  "catalogPath": "~/.openclaw/cabin-photo-catalog/catalog.json",
  "previewDir": "~/.openclaw/cabin-photo-catalog/previews",
  "producerCatalogPath": "~/Repos/cabin/data/photo-catalog/catalog.json",
  "statusPath": "~/.openclaw/cabin-photo-catalog/status.json",
  "model": "codex/gpt-5.6-sol",
  "tagAfter": "2026-09-25",
  "tagBatchLimit": 30
}
```

The scheduled environment pins OpenClaw's managed Node 24 runtime. Homebrew's
older default Node is intentionally not used for image inference.

## Operations

Reconcile and build previews without making model calls:

```bash
~/.openclaw/bin/cabin-photo-catalog.py --reconcile-only
```

Run the normal incremental catalog pass:

```bash
~/.openclaw/bin/cabin-photo-catalog.py
```

Retry prior model failures and low-confidence results in an attended run:

```bash
~/.openclaw/bin/cabin-photo-catalog.py --retag-low-confidence
```

Record or reverse an attended editorial skip using only the hashed asset ID
shown by reconciliation tooling:

```bash
~/.openclaw/bin/cabin-photo-catalog.py --skip-asset icloud-0123456789abcdef01234567
~/.openclaw/bin/cabin-photo-catalog.py --include-asset icloud-0123456789abcdef01234567
```

The skip changes only the producer projection. It never removes the original
or its private derivative.

Read content-free health:

```bash
~/.openclaw/bin/cabin-photo-catalog.py --status
```

## Verification

```bash
python3 -m py_compile \
  openclaw/bin/cabin-photo-catalog.py \
  openclaw/tests/test_cabin_photo_catalog.py
python3 -m unittest -v openclaw.tests.test_cabin_photo_catalog
plutil -lint openclaw/launchagents/ai.openclaw.cabin-photo-catalog.plist
```

After deployment, compare the installed script and plist with their tracked
sources, run one attended pass, read the safe status, and confirm the
LaunchAgent's last exit status.
