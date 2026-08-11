# Camera Wall Dashboard

**Status:** Planned — specification only

**Port:** 8559

**URL:** `http://dylans-mac-mini:8559`
**Service:** `ai.openclaw.camera-dashboard`

## Overview

The Camera Wall is a dedicated, image-first dashboard for the installed Cabin
and Crosstown cameras. It uses nearly all available viewport space for camera
output and keeps navigation and status intentionally compact.

Version 1 is a **fresh-still wall**, not a continuous live-video wall. The
current provider contracts have different capabilities: Nest negotiates a
WebRTC session to extract one JPEG, Ring offers a cloud snapshot that can fail
while a battery device sleeps, and the Reolink helper exposes bounded fresh
captures but not live video. The dashboard presents those sources consistently
without implying a stream that does not exist.

The initial location selector has exactly three values:

- `Both` — default; show every installed enabled camera.
- `Cabin` — show Cabin cameras only.
- `Crosstown` — show Crosstown cameras only.

Opening or reloading the page reads the latest protected dashboard cache. It
does not capture a new frame or wake a battery camera. Fresh capture is always
an explicit user action or a session-scoped auto-refresh choice.

## Goals

1. Devote most screen real estate to current camera images.
2. Make Cabin, Crosstown, and combined views one click apart.
3. Normalize provider differences into honest `ready`, `refreshing`, `stale`,
   `unavailable`, and `no_frame` states.
4. Preserve the last good frame when a refresh fails while clearly showing its
   age and the new failure.
5. Reuse exact existing camera bindings; never accept provider identifiers,
   arbitrary aliases, or output paths from the browser.
6. Avoid unintended battery drain, provider throttling, and concurrent capture
   storms.
7. Keep camera images owner-private, out of browser caches, logs, the event bus,
   and ordinary dashboard status responses.
8. Remain independent of Home Events camera evidence, the Cabin entry
   verifier, image analysis, and message delivery.

## Non-goals

- Continuous video, HLS/WebRTC playback, recordings, event history, or clip
  download.
- Audio, talk, siren, PTZ, zoom, spotlight, recording, notification, firmware,
  account, enrollment, or sharing controls.
- Image analysis, person recognition, plant commentary, or model-generated
  captions.
- A historical image gallery, timeline, or event-bus evidence viewer.
- Proactive captures when no dashboard session explicitly enables refresh.
- Using a camera frame as presence, vacancy, access, or security authority.
- Exposing Flower Cam #2 before it is installed and promoted in exact protected
  configuration.
- Replacing the smaller snapshot controls in the Home Control Plane.

## Installed Camera Inventory

The dashboard uses stable dashboard IDs and safe bindings only. Provider IDs,
resource paths, account identifiers, and raw discovery results never enter the
tracked inventory or API.

| Order | Site | Display name | Provider | Dashboard ID | Existing safe binding |
|------:|------|--------------|----------|--------------|-----------------------|
| 1 | Cabin | Driveway | Ring | `cabin-ring-driveway` | site `cabin`, alias `driveway` |
| 2 | Cabin | Front Door | Ring | `cabin-ring-front-door` | site `cabin`, alias `front_door` |
| 3 | Cabin | Kitchen | Nest | `cabin-nest-kitchen` | exact config alias `Kitchen` |
| 4 | Cabin | Flower Cam #1 | Reolink | `cabin-reolink-flower-cam-1` | exact alias `Flower Cam #1` |
| 5 | Crosstown | Front Door | Ring | `crosstown-ring-front-door` | site `crosstown`, alias `front_door` |
| 6 | Crosstown | Laundry | Nest | `crosstown-nest-laundry` | exact config alias `Laundry` |
| 7 | Crosstown | Living Room | Nest | `crosstown-nest-living-room` | exact config alias `Living Room Wired` |

Flower Cam #2 remains a reserved, uninstalled alias. It is absent from the
dashboard inventory, API, layout, refresh-all work, and health counts until an
attended installation updates the exact camera configuration and this table.

## User Experience

### Page frame

The page has two visual regions:

1. A compact sticky header, approximately 52–60 pixels high.
2. A camera grid that fills the remaining viewport width and height.

The header contains only:

- `Camera Wall` title;
- the `Both / Cabin / Crosstown` segmented selector;
- `Refresh visible`;
- an auto-refresh selector: `Off`, `1 min`, `5 min`, `15 min`;
- a compact aggregate state such as `7 cameras · 6 current · 1 stale`.

Provider details, diagnostics, and timestamps live on camera cards rather than
in additional dashboard panels. There are no charts, sidebars, device-control
forms, event feeds, or large summary cards.

### Camera grid

Cards use a consistent 16:9 image viewport with `object-fit: contain` by
default so a provider's full frame is not silently cropped. Neutral letterbox
space uses the dashboard background. A user may toggle a card to `Fill` for
the current browser session; that preference is visual only and does not alter
the source image.

Recommended desktop layouts:

| Filter | Cameras | Layout |
|--------|---------|--------|
| Cabin | 4 | 2 columns × 2 rows |
| Crosstown | 3 | 3 columns when wide; 2 + 1 when narrower |
| Both | 7 | responsive 3-column grid with site group labels and vertical scroll |

At tablet width, use two columns. At phone width, use one column. Cards retain
their configured order within each site. The `Both` view groups Cabin first and
Crosstown second with small sticky site labels; it does not mix cameras by
provider.

### Card chrome

Card chrome overlays the image edges and should consume minimal space:

- top-left: camera display name;
- top-right: provider badge and state indicator;
- bottom-left: site and relative frame age;
- bottom-right: per-camera refresh and full-screen/focus buttons.

Absolute capture time and the latest safe error code appear in a small detail
popover or accessible tooltip. Controls remain keyboard reachable and have
visible focus states. Status is never conveyed by color alone.

### Focus mode

Selecting an image or the focus button opens an in-page full-screen overlay:

- the image receives the maximum available area without cropping by default;
- name, site, provider, age, and state remain in one compact overlay row;
- left/right arrow keys move through the currently filtered cameras;
- `R` refreshes the focused camera;
- `F` toggles Contain/Fill;
- `Escape` closes focus mode.

Focus mode does not request a new capture by itself.

### Location state

The selected filter is represented in the URL as `?site=both`, `?site=cabin`,
or `?site=crosstown`, allowing bookmarks and browser history to work. Missing
or invalid values normalize to `both`. Auto-refresh and Contain/Fill choices
are session-only and are not encoded in the URL or persisted across browser
restarts.

## Refresh Model

### Initial load

1. Fetch safe camera inventory/status.
2. Fetch an image only for a camera that has a valid cached frame.
3. Show `No cached frame` for a camera without one, whether it has never been
   captured or its prior frame expired.
4. Do not enqueue capture work.

### Manual refresh

- `Refresh visible` enqueues only cameras selected by the current location
  filter.
- A card refresh enqueues only that exact dashboard ID.
- Requests return quickly with accepted/already-running status; capture occurs
  in the background.
- The frontend polls status with bounded backoff and reloads image bytes only
  after `image_version` changes.
- Repeated clicks while a camera is queued or running join the existing job
  rather than creating another capture.

### Auto-refresh

Auto-refresh defaults to `Off`. Enabling it applies only to the current browser
session and only to currently visible cameras.

- The minimum selectable interval is one minute.
- The server still enforces per-camera/provider cooldowns.
- Changing the site filter changes the next refresh set; hidden cameras are not
  refreshed.
- A hidden browser tab does not enqueue capture work. The schedule resumes from
  the current time when the page becomes visible; it does not catch up missed
  intervals.
- Closing the last dashboard page ends browser-driven auto-refresh. There is no
  LaunchAgent capture timer.

### Concurrency and cooldown

- One capture may be active per camera.
- At most two captures may be active globally.
- At most one capture may be active per provider.
- The backend enforces a minimum 60-second start-to-start interval per camera,
  even when multiple browser sessions request refresh.
- Provider throttling or a sleeping battery camera produces a safe error and
  preserves the prior good frame.

The implementation may lengthen provider-specific cooldowns after observed
rate or battery evidence, but it may not make them shorter than the shared
minimum without updating this specification and tests.

## Camera States

| State | Meaning | Image behavior |
|-------|---------|----------------|
| `ready` | A cached frame exists and is within the freshness window | Display normally |
| `refreshing` | An exact capture is queued or running | Keep last good frame with progress overlay |
| `stale` | Last good frame is older than 15 minutes | Display with prominent age badge |
| `unavailable` | Latest capture failed with a safe bounded error | Preserve last good frame if present; otherwise placeholder |
| `no_frame` | No valid dashboard frame exists because none has been captured or the prior frame expired | Provider-neutral placeholder and Refresh action |

`unavailable` describes the latest capture attempt, not necessarily permanent
camera health. The UI must distinguish `unavailable with a 4-minute-old frame`
from `unavailable with no frame`.

Safe error codes are allowlisted, for example:

- `camera_asleep`
- `camera_offline`
- `capture_timeout`
- `provider_rate_limited`
- `authentication_required`
- `codec_negotiation_failed`
- `invalid_image`
- `capture_unavailable`
- `internal_error`

Raw exceptions, command output, URLs, provider IDs, filenames, cleanup tokens,
and account details never reach the API or browser.

## Architecture

```text
Browser
  │
  ├── safe inventory/status ───────────────┐
  ├── protected image fetch                │
  └── exact refresh request                v
                                    camera-dashboard.py
                                      │       │
                              bounded queue   protected latest-frame cache
                                      │
                       exact provider adapters (one/provider)
                         │             │              │
                    Ring bound      Nest exact     Reolink exact
                    snapshot        snap-config    dashboard capture
```

The service is a dotfiles-owned threaded Python HTTP server using standard
library components where practical. Capture work runs outside request threads
through one bounded in-memory queue and a small executor. Queue state is not a
durability claim: a service restart safely drops unstarted refresh requests.
The browser can request them again.

The dashboard never reads, copies, or serves frames from
`~/.openclaw/home-events/camera-images/`. Home Events frames retain their own
short-lived evidence policy and cleanup boundary.

## Provider Adapters

### Ring

- Use the existing private exact-binding snapshot boundary with safe
  `(site, alias)` input only.
- Support only Cabin `driveway`, Cabin `front_door`, and Crosstown
  `front_door`.
- Never use the generic latest-device default, provider ID, event recording,
  video URL, or download path.
- Map sleeping battery-device and cloud failures to bounded safe errors.

### Nest

- Use `nest camera snap-config` with the exact protected aliases `Kitchen`,
  `Laundry`, and `Living Room Wired`.
- Do not use generic display-name discovery from a browser-supplied room.
- Allow the existing 10–15 second WebRTC negotiation behavior without holding
  the HTTP request open.
- Preserve `codec_negotiation_failed` as distinct from offline/unavailable.

### Reolink

- Support only exact alias `Flower Cam #1` in version 1.
- Add or reuse a dedicated bounded dashboard-capture contract that produces one
  JPEG for the protected dashboard cache and cleans the helper's ephemeral
  source and cleanup token in `finally`.
- Do not invoke `share`, `describe`, spotlight controls, raw CGI, cloud access,
  device discovery, or a direct-camera fallback.
- A capture may wake the solar-powered battery camera; default-off refresh and
  backend cooldowns contain that effect.

## HTTP API

All responses use JSON except the page and image bytes. Unknown fields and
unknown camera IDs are rejected.

| Method | Route | Purpose |
|--------|-------|---------|
| `GET` | `/` | No-store dashboard page with current process token |
| `GET` | `/api/health` | Safe service, queue, and aggregate provider health; no camera image metadata beyond counts |
| `GET` | `/api/cameras?site=<both|cabin|crosstown>` | Safe filtered inventory and runtime state |
| `POST` | `/api/refresh` | Enqueue all cameras in an exact requested site filter |
| `POST` | `/api/cameras/<dashboard_id>/refresh` | Enqueue one exact camera |
| `GET` | `/api/cameras/<dashboard_id>/image` | Return the latest protected JPEG if present |

The refresh body accepts only `{"site":"both|cabin|crosstown"}`. It does not
accept camera aliases, providers, filenames, URLs, command arguments, refresh
intervals, or output formats. Per-camera routes resolve only configured stable
dashboard IDs.

Safe camera status contains only:

```json
{
  "id": "cabin-ring-driveway",
  "site": "cabin",
  "display_name": "Driveway",
  "provider": "ring",
  "state": "ready",
  "last_attempt_at": "2026-08-08T16:00:00Z",
  "last_success_at": "2026-08-08T16:00:03Z",
  "image_version": "opaque-revision",
  "safe_error_code": null
}
```

No response contains the server image path, provider binding, provider ID,
resource name, cleanup token, command output, account, or credential state.

## Protected Image Cache

Runtime root: `~/.openclaw/camera-dashboard/`

```text
camera-dashboard/                  0700
├── frames/                        0700
│   └── <dashboard-id>.jpg         0600; latest valid frame only
└── state.json                     0600; safe attempts/outcomes only
```

- Validate JPEG magic, dimensions, nonzero bounded size, and decodeability
  before publication.
- Write to a same-directory mode-`0600` temporary file, `fsync`, rename
  atomically, and `fsync` the directory.
- A failed capture never overwrites the last good frame.
- Keep at most one frame per configured camera; there is no history directory.
- Prune disabled/unknown-camera files and frames older than 24 hours at startup
  and every six hours.
- Image responses use `Cache-Control: no-store, private`, a restrictive content
  type, and no filesystem-derived filename.
- Frontend object URLs are revoked when replaced or when a card is removed.

## Network, Browser, and Privacy Boundary

The service follows the existing dashboard deployment model: Mac Mini only,
bound to `0.0.0.0:8559`, reachable on the trusted home LAN and Tailscale
tailnet, and never intentionally exposed to the public internet.

Camera imagery is more sensitive than ordinary dashboard status:

- each process generates a fresh bearer token and embeds it only in the
  no-store root page;
- all API routes except `/api/health` require that token;
- image bytes are fetched with authenticated JavaScript and rendered from a
  short-lived object URL rather than a public `<img src>`;
- refresh requests additionally require same-origin `Origin`/`Host` checks;
- responses advertise no wildcard CORS, refuse cross-origin framing, use a
  restrictive CSP and `Referrer-Policy: no-referrer`, and disable MIME sniffing;
- logs contain only dashboard ID, site, provider, timestamps, duration,
  outcome code, and bounded queue counts—never image data or paths.

This is a trusted-network boundary, not per-person identity authentication.
Anyone allowed to load the page from the trusted LAN or tailnet can obtain the
current process token and view or refresh configured cameras. Supporting a
broader network or per-person access requires a separate authenticated design.

## Interaction With Existing Systems

- **Home Control Plane:** continues to provide smaller Ring/Nest snapshot
  controls. The Camera Wall may reuse capture libraries or cache plumbing, but
  it does not proxy through port 8558 or depend on that service being alive.
- **Home Events:** no shared image files, jobs, policy, health, event records,
  or retention. Dashboard refreshes do not publish camera events.
- **Cabin entry verifier:** retains its ordered driveway/front-door trigger and
  exact Kitchen evidence path independently.
- **Reolink skill:** sharing, commentary, plant tracking, and spotlight remain
  separate explicit workflows.
- **Presence:** may eventually supply a small site badge, but it cannot filter,
  suppress, authorize, or trigger version-1 captures.

## Failure Behavior

- One provider failure does not block status or images from another provider.
- One camera failure does not fail `Refresh visible` for the remaining cameras.
- A queue-full response is explicit and does not discard an already accepted
  job.
- Restarting the service drops queued work, preserves validated latest frames,
  prunes expired frames, and starts with auto-refresh off.
- Invalid configuration fails service startup before binding the port.
- Missing credentials or required reauthentication degrade only the matching
  provider/cameras with a safe error.
- Clock skew, future timestamps, malformed state, unsafe permissions, symlinks,
  hard links, oversized images, and non-JPEG output fail closed.

## Accessibility and Visual QA

- Use semantic buttons and a radiogroup/segmented-control pattern for location.
- Every image has concise alt text: `<site> <camera name>, captured <relative
  time>` or `<site> <camera name>, no captured image available`.
- State changes use an ARIA live region without announcing every polling tick.
- The grid and focus mode are fully keyboard operable.
- Respect reduced-motion preferences; refreshing uses a static progress overlay
  when motion is reduced.
- Maintain WCAG AA contrast for labels, errors, focus rings, and overlays.

During implementation, use PinchTab with a dedicated agent session for layout
verification at desktop, tablet, and phone widths. Do not reuse an OpenClaw or
personal authenticated browser session.

## Test Plan

### Configuration and API

- Exact seven-camera inventory, stable order, unique IDs, known sites/providers,
  and safe bindings.
- Flower Cam #2 absent until explicitly enabled.
- Invalid site filters normalize only on page navigation and are rejected by
  mutation APIs.
- Unknown IDs, extra JSON fields, unsafe methods, arbitrary paths, symlinks,
  provider IDs, and traversal attempts fail before capture.

### Capture and queue

- Per-camera single-flight, provider concurrency of one, global concurrency of
  two, queue capacity, cooldown, polling, and restart behavior.
- Refresh-visible respects the selected site and never captures hidden cameras.
- Page load, filter change, focus mode, and image fetch never trigger capture.
- Failure preserves the last good image/version and records only a safe error.
- Atomic replacement, file modes, image validation, expiry, and pruning.

### Provider contracts

- Ring exact site/alias binding and asleep/rate-limit mapping.
- Nest exact config aliases, timeout, codec failure, and eventual success.
- Reolink exact alias, ephemeral-source cleanup on success/failure/exception,
  and no reachability to sharing, analysis, spotlight, or raw API paths.

### Browser security

- Bearer required for inventory and images.
- Same-origin checks required for refresh mutations.
- No wildcard CORS, public image URL, browser cache, cross-origin frame, token
  persistence, path disclosure, or referrer leakage.
- Process restart invalidates old-page tokens.

### UI

- Both/Cabin/Crosstown URL behavior and camera counts.
- Desktop 2×2 Cabin, three-camera Crosstown, and responsive combined grid.
- Phone single-column layout, focus mode, keyboard navigation, reduced motion,
  alt text, stale/error overlays, and Contain/Fill behavior.

## Implementation and Rollout

1. Add server, exact tracked inventory, provider adapters, tests, and
   documentation without a deployed LaunchAgent.
2. Run Python compilation, focused unit/integration tests, shell syntax where
   applicable, plist lint, diff hygiene, and filesystem-permission tests.
3. Install the server and LaunchAgent on the Mac Mini with auto-refresh off.
4. Verify page load and all three location filters from cached/empty state
   without any provider capture.
5. Perform one attended exact capture per provider, one camera at a time;
   verify image display, timestamps, cleanup, modes, and no event-bus artifact.
6. Exercise refresh-visible separately for Cabin, Crosstown, and Both while
   observing concurrency and provider cooldowns.
7. Complete PinchTab visual QA, restart recovery, and a 24-hour cache-prune
   check before declaring the dashboard active.

There is no production auto-refresh rollout. Users opt into it per browser
session, and `Off` remains the default after reload or service restart.

## Rollback

1. Stop and boot out only `ai.openclaw.camera-dashboard`.
2. Preserve logs long enough for sanitized diagnosis.
3. Move or remove only the exact protected camera-dashboard runtime directory
   after verifying the service is stopped; do not touch Home Control Plane,
   Home Events, Nest history, provider credentials, or helper configuration.
4. No camera setting, event policy, or provider state requires compensation
   because the dashboard performs still capture only.

## Expected Tracked Files

- `openclaw/plans/camera-dashboard.md`
- `openclaw/DASHBOARDS.md`
- `openclaw/bin/camera-dashboard.py`
- `openclaw/launchagents/ai.openclaw.camera-dashboard.plist`
- exact camera-dashboard configuration under `openclaw/`
- provider-bound helper changes only where required for a safe dashboard JPEG
- focused tests under `openclaw/tests/`
- `openclaw/LAUNCHAGENTS.md`
- `openclaw/bin/README.md`
- `openclaw/logs/README.md`

## Definition of Done

- The page defaults to `Both` and dedicates the viewport primarily to the seven
  installed camera outputs.
- Cabin and Crosstown filters show exactly their configured four and three
  cameras in stable order.
- Cached images load without a capture; explicit refresh is exact, bounded,
  deduplicated, provider-aware, and honest about failure.
- No frame history, public image route, provider identifier, arbitrary target,
  image path, token, raw error, or event-bus evidence enters the UI or logs.
- Ring, Nest, and Reolink each pass an attended exact capture and cleanup check.
- Sleeping/offline cameras preserve their last good frame and do not degrade
  unrelated cards.
- Focus mode and responsive layouts pass keyboard, accessibility, and visual
  QA at desktop, tablet, and phone sizes.
- The LaunchAgent is healthy on the Mac Mini, the service survives restart,
  and rollback affects no other camera or home-event workflow.
