# iCloud Shared Album Ingest

**Schedule:** Daily at approximately 9:15 AM local time

**Service:** `ai.openclaw.icloud-shared-album-ingest`

The iCloud Shared Album ingest service is a deterministic, non-model data
pump. It reads one or more albums whose **Public Website** option is enabled,
downloads assets that have not been seen before, updates local deduplication
state, and exits. It does not publish a home event, notify another service, or
require an Apple ID.

## What It Does

- Resolves the public token from each configured
  `https://share.icloud.com/photos/<token>` URL.
- Reads album metadata through Apple's unauthenticated `webstream` endpoint,
  following the partition redirect when Apple supplies one.
- Requests short-lived download URLs in batches of at most 25 asset GUIDs.
- Chooses the largest advertised derivative and writes it atomically as
  `<creationDateIsoCompact>-<assetGuid>.<ext>`.
- Records each completed GUID in a hidden JSON state file in the inbox. A
  repeated or overlapping run does not download that GUID again.

Every HTTP operation has a 20-second timeout and one retry. Signed download
URLs are used only in memory and are never logged or persisted.

## Configuration

The first run creates the following empty config and exits successfully:

```json
{"albums": []}
```

Add one object per public album:

```json
{
  "albums": [
    {
      "url": "https://share.icloud.com/photos/XXXXX",
      "inboxDir": "~/Pictures/openclaw-icloud-inbox/julia-cabin-share"
    }
  ]
}
```

`url` must be an HTTPS public iCloud Shared Album URL. `inboxDir` is optional;
when omitted, the default is
`~/Pictures/openclaw-icloud-inbox/<slug-of-token>`.

The public-website toggle is the entire authentication boundary. Do not add an
Apple ID, OAuth token, Photos library access, browser profile, or other signed-in
iCloud path to this service.

## State and Idempotency

Each inbox contains `.icloud-shared-album-ingest.state.json`, keyed by the
iCloud asset GUID. Downloads use an inbox-local temporary file followed by an
atomic rename; state is then replaced atomically. If a run is interrupted
between those operations, the next run reconciles the already-written
timestamp/GUID file into state instead of downloading it again.

The config directory also contains `.ingest.lock`. A second process exits zero
when another ingest is active, so a manual run cannot race the daily job.

## Files

| File | Path |
|------|------|
| Script source | `openclaw/bin/icloud-shared-album-ingest.py` |
| Deployed script | `~/.openclaw/bin/icloud-shared-album-ingest.py` |
| Config | `~/.openclaw/icloud-shared-album-ingest/config.json` |
| Default inbox root | `~/Pictures/openclaw-icloud-inbox/` |
| Per-inbox dedup state | `<inboxDir>/.icloud-shared-album-ingest.state.json` |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.icloud-shared-album-ingest.plist` |
| Logs | `~/.openclaw/logs/icloud-shared-album-ingest.{log,err.log}` |

## Logs and Exit Status

Log lines use UTC timestamps and grep-friendly fields:

```text
2026-10-12T13:15:00Z level=info album=xxxxx event=album_complete discovered=12 downloaded=2 recovered=0 failed=0
```

An empty config logs `no albums configured` and exits 0. Album metadata,
download, config, or state failures log a bounded reason and make the overall
run exit nonzero. Full signed URLs and response bodies are never logged.

## Verification

```bash
python3 -m py_compile \
  openclaw/bin/icloud-shared-album-ingest.py \
  openclaw/tests/test_icloud_shared_album_ingest.py
plutil -lint \
  openclaw/launchagents/ai.openclaw.icloud-shared-album-ingest.plist
python3 -m unittest -v openclaw.tests.test_icloud_shared_album_ingest
```

Installing or bootstrapping the LaunchAgent is an attended deployment step and
is not performed merely by updating this repository.
