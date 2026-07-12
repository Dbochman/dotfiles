# Nest SDM event listener

The Nest event listener is a multi-camera Google Device Access Pub/Sub
consumer for the Mac Mini. The listener itself remains deliberately
**shadow-only**: it receives, validates, deduplicates, and records motion and
person metadata without doing slow camera, model, or delivery work in the
Pub/Sub acknowledgement path.

A separate Cabin activity reviewer consumes that durable shadow outbox for
exactly **Kitchen / Cabin**. It may capture a fresh live frame, ask OpenClaw's
stateless vision capability for a factual observation, and send one text-only
iMessage only while the presence system confirms the Cabin is vacant. An
occupied or uncertain Cabin and both Crosstown cameras remain shadow-only.

## Data flow

```text
Kitchen (Cabin) ---------\
Living Room (Crosstown) --+-> SDM topic -> pull subscription -> Mac Mini
Living Room Wired -------/                            |
                                                       +-> protected SQLite/status
                                                                   |
                                      Kitchen/Cabin only -----------+
                                                                   v
                                         cached + live presence gate
                                                                   |
                                                     confirmed vacant only
                                                                   v
                                            fresh frame -> OpenClaw vision
                                                                   |
                                                meaningful + hourly slot
                                                                   v
                                                        text-only iMessage
```

One Device Access topic and one pull subscription cover every camera that is
authorized to the same Device Access project. Per-camera behavior is selected
from an owner-only exact-resource configuration, not fuzzy room matching.

## Cloud contract

The attended setup created these resources:

- topic: `nest-sdm-events`
- pull subscription: `openclaw-nest-events`
- subscriber service account: `openclaw-nest-events`
- Device Access event publishing: enabled

The Device Access publisher has `Pub/Sub Publisher` only on the topic. The
listener service account has `Pub/Sub Subscriber` only on the subscription and
no project-wide role. The subscription uses a 60-second acknowledgement
deadline, one-day unacknowledged-message retention, immediate retry, and
at-least-once delivery. Durable application deduplication is therefore still
required.

## Protected runtime contract

```text
~/.openclaw/nest-events/                 0700
├── credentials/                         0700
│   └── subscriber-service-account.json  0600
├── config/                              0700
│   └── cameras.json                     0600
└── state/                               0700
    ├── events.sqlite3                   0600
    ├── status.json                      0600
    ├── activity-reviewer.json           0600
    ├── activity-reviewer.lock           0600
    └── activity-images/                 0700 (normally empty)

~/.openclaw/venvs/nest-events/           dedicated locked Python environment
~/.openclaw/logs/nest-event-listener*    bounded 0600 operational logs
~/.openclaw/logs/nest-activity-reviewer* bounded 0600 operational logs
```

The credential JSON is never stored in the repository, general secrets cache,
environment contents, command arguments, or logs. The LaunchAgent receives
only its protected file path, has an allowlisted environment, and never calls
`op`, `gcloud`, or `uv`.

The owner-only configuration has this schema, with real SDM resource names in
place of the placeholders:

```json
{
  "version": 1,
  "cameras": [
    {
      "alias": "Kitchen",
      "site": "Cabin",
      "resource": "enterprises/REDACTED/devices/REDACTED",
      "capture": "live"
    },
    {
      "alias": "Living Room",
      "site": "Crosstown",
      "resource": "enterprises/REDACTED/devices/REDACTED",
      "capture": "event"
    },
    {
      "alias": "Living Room Wired",
      "site": "Crosstown",
      "resource": "enterprises/REDACTED/devices/REDACTED",
      "capture": "event"
    }
  ]
}
```

Kitchen uses the already-proven live WebRTC frame path because it lacks the
`CameraEventImage` trait. Living Room and Living Room Wired expose event
images, whose inner event IDs expire after 30 seconds. Shadow mode records only
the intended capture strategy; it never retains an image token or raw SDM
identifier.

## Initial attended deployment

Confirm the target first:

```bash
hostname
whoami
echo "$HOME"
```

Expected values are `mac-mini`, `dbochman`, and `/Users/dbochman`.

1. Create every protected runtime directory before materializing either private
   file. `install -d` is idempotent and applies the required mode without
   putting identifiers or credentials in shell arguments:

   ```bash
   install -d -m 0700 \
     "$HOME/.openclaw/nest-events" \
     "$HOME/.openclaw/nest-events/credentials" \
     "$HOME/.openclaw/nest-events/config" \
     "$HOME/.openclaw/nest-events/state"
   ```

   Verify that all four are real, owner-owned directories with mode `0700`
   before continuing. The listener and its wrapper fail closed rather than
   repairing missing or insecure runtime directories.

2. Create the service-account JSON key only after the subscription-level IAM
   binding exists. Store its complete JSON in one exact 1Password field and
   add that field's reference as
   `OP_REF_NEST_EVENTS_SERVICE_ACCOUNT_JSON` in the owner-only mode-`0600`
   `~/.openclaw/.secrets-refresh.env`.

3. Materialize the credential through the attended helper:

   ```bash
   "$HOME/dotfiles/openclaw/bin/openclaw-refresh-secrets" --interactive
   ```

   The helper validates the expected service-account identity and Google token
   endpoint, writes through a same-directory mode-`0600` temporary file, and
   preserves the previous credential on failure.

4. Resolve the three exact resource names from `/opt/homebrew/bin/nest raw`
   without printing or logging them. Write `cameras.json` atomically with mode
   `0600`; step 6 validates it through the deployed wrapper after its locked
   interpreter is available. Also verify the discovered traits: all three need
   `CameraMotion` and `CameraPerson`; Kitchen needs `CameraLiveStream`; Living
   Room and Living Room Wired need `CameraEventImage`.

5. Build the frozen runtime environment from the tracked lock:

   ```bash
   UV_PROJECT_ENVIRONMENT="$HOME/.openclaw/venvs/nest-events" \
     /opt/homebrew/bin/uv sync --project "$HOME/dotfiles/openclaw/nest-events" \
       --frozen --no-dev
   ```

6. Deploy `nest-event-listener.py` and its wrapper to
   `~/.openclaw/bin/`, copy the plist to `~/Library/LaunchAgents/`, compare the
   deployed files to their tracked sources, and run:

   ```bash
   ~/.openclaw/bin/nest-event-listener-wrapper.sh check-config
   plutil -lint ~/Library/LaunchAgents/ai.openclaw.nest-event-listener.plist
   launchctl bootstrap "gui/$(id -u)" \
     ~/Library/LaunchAgents/ai.openclaw.nest-event-listener.plist
   ```

Routine dotfiles pulls update the scripts and plist and reload an already
loaded listener. They never create the credential, config, venv, or initial
LaunchAgent registration.

## Cabin commentary policy

The active reviewer has a deliberately narrow contract:

- Only exact `Kitchen` / `Cabin` / `live` rows can trigger capture. `Living
  Room` and `Living Room Wired` are advanced without capture, inference, or
  delivery.
- Presence is a per-site hard gate, not a global activity switch. Cabin is
  active only when the correlated `state.json` says `confirmed_vacant`, both
  location inputs and their raw scans are independently fresh, Dylan and Julia
  are consistently assigned to Crosstown, and no fresh Cabin scan shows either
  resident. `occupied`, `possibly_vacant`, `unknown`, stale, missing,
  malformed, future-dated, writable, or inconsistent presence data all means
  shadow mode.
- The Nest event time must be on or after the Cabin vacancy transition. This
  prevents departure motion queued just before vacancy from being replayed as
  activity after the house changes mode.
- Immediately before capture, the reviewer runs only the presence skill's
  side-effect-free `presence-detect.sh observe cabin` path. A resident seen on
  the Cabin network or any observation failure vetoes the review. It never runs
  the state-writing `cabin` or `evaluate` modes. Cached presence is checked
  again after capture and immediately before delivery; a newly occupied home
  discards the frame/commentary without reserving a message slot.
- A trigger waits eight seconds for the scene to settle and is discarded if it
  is over two minutes old. Repeated review work is limited to once per five
  minutes.
- The model receives only the fresh image and a fixed prompt through
  `openclaw infer image describe`; it gets no agent session, tools, household
  memory, chat transcript, or delivery capability. Local orchestration still
  sends the image to the pinned OpenAI model through OpenClaw's Codex
  app-server image route.
- Empty, static, blurry, uncertain, or malformed results are silent. A valid
  result must be medium/high confidence, factual, single-line, and grounded in
  visibly present people, animals, work/delivery activity, or a safety concern.
- Messages describe the image, never the raw Nest event. The deterministic
  prefix is `Cabin kitchen:`; no image is attached.
- The hard rolling limit is one **send attempt** per 3,600 seconds. The slot is
  written and fsynced before invoking iMessage. A send failure, timeout, or
  crash therefore burns the hour rather than risking a duplicate.
- Frames use random names in the owner-only image directory, must validate as
  a fresh mode-`0600` JPEG, and are deleted on every normal outcome. Startup
  removes a crash orphan before processing another event. No model commentary,
  message body, chat target, image path, or raw SDM identifier is persisted or
  logged; only a one-way summary hash and safe counters remain.

The wrapper reads the protected cache only in a short-lived subshell to derive
the numeric Dylan chat target, then starts the reviewer with `env -i`. It never
calls `op` and never gives the reviewer the rest of the secrets cache.

The current activation policy is intentionally asymmetric: a vacant Cabin may
be active while occupied Crosstown remains shadow. Future Crosstown activation
must use its own presence gate and camera-specific acceptance test; vacancy at
one home never enables cameras at the other.

## Explicit on-demand camera media

Trusted owners may explicitly request a fresh still or a 1-30 second live clip
from any of the three configured cameras, independently of presence and
proactive monitoring mode. The `nest-camera` skill resolves natural language
to one of the three canonical aliases, while `nest-camera-image` captures only
the corresponding protected exact resource. A clip request records the live
WebRTC video track into an H.264/yuv420p MP4; it does not retrieve historical
Nest Aware footage. Unspecified short clips default to 10 seconds.

The helper stages mode-`0600` JPEG or MP4 media in the owner-only OpenClaw
media tree, validates its structure and freshness, sends it through the
current conversation's native message-tool route, and deletes it immediately
after that synchronous send returns. A fixed TTL reaper and startup sweep
remove crash orphans.

This path never reuses the activity reviewer's image, fuzzy-matches a mutable
Google display name, or retrieves a historical event frame or recording.
Requests must be private, one-to-one, explicitly current, and attributable to
Dylan or Julia; group, third-party, and unverified requests are refused before
capture. Asking for old media gets an explanation that monitoring frames are
not retained and an offer to capture a fresh image or live clip.

```bash
nest-camera-image capture 'Kitchen'
nest-camera-image capture-video 'Kitchen' 10
nest-camera-image cleanup '<opaque-token>'
```

The cleanup command is mandatory after delivery, delivery failure, or timeout.
Callers pass only the helper-returned opaque token, never an arbitrary path.

### Attended reviewer activation

Deploy and baseline before bootstrap so historical Crosstown validation rows
cannot cause a review:

```bash
install -m 0755 \
  "$HOME/dotfiles/openclaw/bin/nest-camera-snap.py" \
  "$HOME/.openclaw/bin/nest-camera-snap.py"
install -m 0755 \
  "$HOME/dotfiles/openclaw/bin/nest-activity-reviewer.py" \
  "$HOME/.openclaw/bin/nest-activity-reviewer.py"
install -m 0755 \
  "$HOME/dotfiles/openclaw/bin/nest-activity-reviewer-wrapper.sh" \
  "$HOME/.openclaw/bin/nest-activity-reviewer-wrapper.sh"
install -m 0644 \
  "$HOME/dotfiles/openclaw/launchagents/ai.openclaw.nest-activity-reviewer.plist" \
  "$HOME/Library/LaunchAgents/ai.openclaw.nest-activity-reviewer.plist"

~/.openclaw/bin/nest-activity-reviewer-wrapper.sh check-config
~/.openclaw/bin/nest-activity-reviewer-wrapper.sh initialize
plutil -lint \
  ~/Library/LaunchAgents/ai.openclaw.nest-activity-reviewer.plist
launchctl bootstrap "gui/$(id -u)" \
  ~/Library/LaunchAgents/ai.openclaw.nest-activity-reviewer.plist
```

The first `initialize` records the current maximum listener outbox row as its
baseline. A routine pull updates and reloads this job only after its plist has
already been installed; it never silently performs first activation.

## Operations

Read the safe status projection:

```bash
~/.openclaw/bin/nest-event-listener-wrapper.sh status | jq .
~/.openclaw/bin/nest-activity-reviewer-wrapper.sh status | jq .
launchctl print "gui/$(id -u)/ai.openclaw.nest-event-listener" \
  | grep -E 'state =|pid =|last exit code'
```

Status and logs may contain aliases, sites, event classes, timestamps,
counters, and sanitized error codes. They must never contain raw payloads,
resource names, SDM/user/event IDs, credential fields, image URLs, chat
targets, or message bodies. `status` and `check-config` write their safe JSON to
the invoking terminal. Only `run` writes service logs; its streaming log writer
continuously caps each file rather than waiting for a listener restart.

For a controlled single pull while the long-running job is stopped:

```bash
~/.openclaw/bin/nest-event-listener-wrapper.sh run --once
```

Do not run the one-shot consumer concurrently with the LaunchAgent.

## Rollout gates

1. Keep shadow mode for 24–48 hours and confirm healthy pulls, durable restart
   dedupe, bounded logs, and no subscription backlog.
2. Perform an attended motion/person test in front of each of the three
   cameras. A devices-list resource update proves Pub/Sub connectivity but not
   camera-event delivery.
3. Keep Cabin commentary presence-gated with the fixed one-hour cap. Validate
   occupied presence performs zero visual work and a confirmed-vacant empty
   Cabin frame is silent before performing a meaningful physical event.
4. Keep Crosstown shadow-only until Cabin survives restart, a failed delivery
   simulation, and a full one-hour retrigger test. Kitchen live capture must
   preserve the required H.264 `42e01f` negotiation.
5. The temporary July 13–17 Cabin snapshot watch was retired after the active
   presence gate, exact-resource capture, restart behavior, and empty-Cabin
   silence passed. Pub/Sub plus the reviewer now owns that monitoring path;
   explicit image requests use the separate ephemeral flow above.

Current physical validation:

- **Living Room Wired / Crosstown:** person event passed on 2026-07-11. The
  first event was durably accepted and its follow-up thread update was
  consolidated without a duplicate shadow action. A later person event while
  Crosstown was occupied was advanced by the active reviewer with zero
  capture, inference, or delivery, confirming Crosstown remains shadow.
- **Kitchen / Cabin:** the exact resource-bound live capture, correlated vacant
  gate, live network veto, Codex image route, and high-confidence empty-frame
  silence passed on 2026-07-11. A meaningful physical event is still pending.
- **Living Room / Crosstown:** pending; the camera was unavailable during the
  first attended attempt.

Pub/Sub acknowledgement occurs only after the delivery and shadow outbox
decision commit to SQLite. Malformed or unsupported messages are safely
tombstoned and acknowledged so they cannot become poison loops. The reviewer
is outside that path. Its reserve-before-send rule prefers an occasional
missed comment over a duplicate or burst of messages.

## Credential rotation

Create a new key, update the exact 1Password field, run the attended refresh,
restart the listener, and prove a live pull before disabling the previous key.
Observe the shadow listener before deleting the old key. Never rotate by
placing JSON in `.secrets-cache`, a plist, shell history, or a LaunchAgent
environment.

To stop the listener without deleting durable state:

```bash
launchctl bootout \
  "gui/$(id -u)/ai.openclaw.nest-event-listener"
```

Deleting a service-account key, subscription, or topic is a separate cloud
change and requires explicit confirmation.

## References

- [Subscribe to Device Access events](https://developers.google.com/nest/device-access/subscribe-to-events)
- [SDM event envelopes, threads, and filtering](https://developers.google.com/nest/device-access/api/events)
- [Camera event-image lifetime and download flow](https://developers.google.com/nest/device-access/traits/device/camera-event-image)
- [Pub/Sub subscription and delivery semantics](https://docs.cloud.google.com/pubsub/docs/subscription-overview)
- [Google service-account key guidance](https://docs.cloud.google.com/iam/docs/best-practices-for-managing-service-account-keys)
