# Long-form video preparation

Use this workflow to turn long, private property footage into reviewable named
sections and ordered COLMAP inputs without modifying the originals.

## Review package

`remote-splat video-review` accepts up to 16 repeated `--source` arguments and
creates a new local directory containing:

- `index.html`: a mobile-friendly grid of thumbnails with source timecodes;
- `review.json`: probe metadata and the thumbnail/timecode mapping;
- `segments.template.json`: source paths, source SHA-256 values, and empty
  segment lists;
- optional silent, metadata-free 720p review proxies; and
- optional PySceneDetect CSV suggestions for actual shot changes.

Use a 20–30 second thumbnail interval for long walking footage. Reduce it near
short interior transitions. PySceneDetect is useful for edits, camera stops,
or abrupt exposure changes, but continuous walking footage usually requires
human semantic boundaries. When a review proxy is requested, scene detection
runs against that proxy to avoid decoding the 4K original a second time; its
timecodes still map to the source recording.

The template contains private absolute paths and should remain in an
owner-readable working directory. Copy it to `segments.json`; do not edit the
template if you want to retain a clean starting point.

## Segment manifest

The schema is deliberately small:

```json
{
  "version": 1,
  "defaultFps": 3.0,
  "maxWidth": 0,
  "includeClips": true,
  "videos": [
    {
      "id": "video-01",
      "source": "/absolute/path/trail-one.mov",
      "sha256": "generated-by-video-review",
      "durationSeconds": 1234.5,
      "segments": [
        {
          "name": "trail-north-outbound",
          "start": "00:03:10.000",
          "end": "00:08:45.000",
          "fps": 3.0
        }
      ]
    }
  ]
}
```

Names must be unique lowercase letters, numbers, and hyphens. Timecodes accept
seconds or `HH:MM:SS.mmm`. Adjacent regions may deliberately overlap; reuse a
short boundary interval under both names when it contains the stable landmarks
needed to connect them.

Use 2–3 fps for smooth outdoor walking and 3–4 fps for tighter interiors or
faster turns. More frames are not automatically better. The helper caps a
manifest at 100 segments and 100,000 estimated frames.

`maxWidth: 0` retains the decoded source resolution. Set a nonzero width only
after confirming that transfer/storage is more important than retaining fine
detail. Brush can downsample during training, so the first extraction should
normally preserve source resolution.

## Extraction outputs

`video-extract --dry-run` validates every source, time range, frame rate, and
estimated frame count without creating the output directory. The actual run
recomputes each source SHA-256 and stops if footage changed after review.
The estimate uses segment duration × cadence; FFmpeg's timestamp rounding can
legitimately make the decoded inventory differ by a frame at a boundary. Treat
the per-segment `frameCount` values in `extraction.json` as authoritative,
require them to agree with the files on disk, and use a narrow predeclared
total-count bound rather than asserting that the estimate is exact.

The new output directory contains:

```text
clips/<segment>.mp4
frames/<segment>/<segment>-000001.jpg
frames/<segment>/frames.csv
extraction.json
```

For a multi-gigabyte source, `remote-splat inbox-stage` can use the desktop's
private direct Tailscale path when the guarded SSH copy is materially slower.
If the source already resides in Dylan's Mac Downloads directory, use
`workstation-inbox-stage` so that Mac sends it directly to the desktop and the
Mini handles only guarded orchestration. The helper verifies the remote source
size and digest before and after transfer; the desktop preparation job must
still bind and independently verify that exact digest before ingest.
Serialize large workstation transfers. Multiple simultaneous Taildrop sends
share the same constrained path and usually reduce useful throughput; prepare
and review an already delivered source while the next source transfers instead.
When the workstation has no Tailscale CLI, this command may relay the byte
stream through the Mini's Taildrop client without writing an intermediate
file. Treat that as a transport fallback, not a weaker ingest contract.
The remote preparation script must verify the recorded SHA-256 before bringing
that Taildrop inbox file into the managed job root. On a current Windows
receiver, address the exact file under `C:\Users\<user>\Downloads`; do not call
an unfiltered `tailscale file get`, because that could move unrelated inbox
items. Preserve the received source and copy through a hash-verified temporary
file before atomically promoting it into the job.

Treat the successful source-hash and copied-temporary-hash checks as the ingest
receipt. Do not immediately read the same multi-gigabyte file a third time from
the Windows filesystem through WSL merely to reproduce that digest; bind later
steps to the receipt plus exact path and size, and reserve another full hash for
artifact handoff or when provenance has become ambiguous.

Clips are frame-accurate H.264 review/reference copies with audio and metadata
removed. Modeling frames are high-quality JPEGs extracted directly from the
original source at the requested cadence. `frames.csv` maps ordered filenames
to approximate source timecodes, and `extraction.json` records counts and clip
hashes.

The helper does not automatically delete blurry or repetitive frames. Review
the grid after extraction and make an explicit selection: trail foliage,
ground texture, and rapid turns can fool generic blur and duplicate metrics.
Keep enough neighboring frames that every stable feature appears from several
translated viewpoints.

For sparse review sheets from an indexed long video, seek directly to each
sample time and decode one thumbnail rather than decoding every intervening 4K
frame through an `fps` filter. Tile those small thumbnails afterward. This is
both faster and easier to resume, and it keeps review generation independent
from the later modeling-frame decode.

## Modeling handoff

Stage the selected `frames/` tree rather than the full source videos unless the
desktop needs to repeat extraction. Preserve each segment subdirectory so
COLMAP can use sequential matching within a clip and targeted cross-segment
matching around the overlapping boundaries.

Keep exterior-anchor, backyard, interior-transition, interior-room, and trail
segments separate through registration. Merge them for Brush only after the
COLMAP camera graph proves they share one reconstruction.
