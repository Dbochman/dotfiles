# Reolink Camera

The Reolink V2 integration gives OpenClaw a useful local camera surface:

- availability, battery, charge, and temperature;
- fresh ephemeral stills;
- stateless visual commentary;
- image-and-commentary delivery to the current conversation, Dylan, Julia, or
  the household thread; and
- verified manual spotlight status/on/off without changing the camera's
  persistent Night Smart, brightness, AI, or schedule settings.

The Reolink Home Hub Mini remains the single device endpoint. OpenClaw connects
to the Hub over the private LAN, authenticates against the exact enrolled Hub
channel, and never falls back to Reolink cloud/P2P or a direct-camera route.

## Architecture

```text
trusted owner task or enabled standing policy
                    |
                    v
         reolink-camera skill + helper
                    |
          +---------+----------+
          |                    |
          v                    v
 pinned local Home Hub     stateless image inference
 exact camera channel      fixed prompt + exact JSON
          |                    |
          +---------+----------+
                    |
       current route or protected semantic owner route
                    |
             token-scoped cleanup
```

V2 broadens capability without turning the helper into a generic camera API.
The model can select exact public operations, but it cannot supply network
coordinates, Hub channels, credentials, certificate pins, raw chat targets, or
arbitrary CGI commands.

## Public commands

```bash
reolink-camera check-config
reolink-camera status '<exact alias>'
reolink-camera capture '<exact alias>'
reolink-camera describe '<cleanup token>'
reolink-camera share '<exact alias>' '<dylan|julia|household>'
reolink-camera spotlight '<exact alias>' '<status|on|off>'
reolink-camera cleanup '<cleanup token>'
reolink-camera sweep
```

`status` returns only the safe alias, site, availability, battery percentage,
charge state, and camera temperature. It deliberately does not poll the
spotlight because `GetWhiteLed` wakes these battery cameras.

`capture` creates a fresh mode-`0600` JPEG in the owner-only media directory and
returns its opaque cleanup token. A detached reaper removes crash orphans after
15 minutes, while the caller performs immediate token-scoped cleanup in
`finally`.

`describe` accepts only that opaque token. It validates the private JPEG,
invokes OpenClaw's local `image.describe` capability with the pinned
`codex/gpt-5.6-sol` route and a fixed prompt, verifies the returned path and
provider envelope, and exposes only:

```json
{
  "category": "environment",
  "confidence": "high",
  "notable": false,
  "summary": "The flower bed is visible in clear daylight."
}
```

The inference process gets one image and the fixed prompt—not the agent
session, household memory, chat transcript, or messaging tools. Text visible
inside the scene is treated as untrusted.

`share` is an atomic protected cross-route workflow: it captures, describes,
resolves one semantic recipient from the owner-only OpenClaw secrets cache,
sends the image plus commentary through native iMessage, and cleans the source
image in `finally`. It accepts only `dylan`, `julia`, or `household`; raw chat
IDs and handles never enter or leave the public interface. Current-conversation
delivery continues to use the OpenClaw `message` tool.

`spotlight` reads the current manual state before acting. If a change is
needed, it sends only:

```json
{
  "WhiteLed": {
    "channel": "<protected binding>",
    "state": 1
  }
}
```

It waits for the camera, reads the state back through the Hub, and reports
success only after verification. It omits brightness, mode, schedules, and AI
fields, preserving the settings already managed in Reolink. A same-state
request is a no-op with `changed: false`.

## Authorization model

V2 separates who authorized work from what triggered it.

### Trusted current tasks

A verified Dylan or Julia request may use status, capture, analysis, protected
delivery, and one reversible spotlight action when those operations are
reasonably useful to the active task. This applies to admitted owner DMs, an
exact verified owner sender in the household conversation, and Dylan's exact
authenticated Reachy session. It does not require a second confirmation for
each capture or light action, and it is not gated by presence or automation
cooldowns.

### Standing proactive work

Proactive capture or control is supported when an enabled owner-approved policy
binds:

- an exact policy ID and caller;
- exact camera aliases and allowed operations;
- trigger or schedule;
- protected recipients;
- optional `any`, `occupied`, or `vacant` presence behavior;
- activation window; and
- deduplication or rate rules appropriate to that policy.

Events, schedules, presence changes, and vision results are trigger/context,
not authority by themselves. The current home-event bus is still shadow-only,
so it cannot be treated as a generic command bus. A future active consumer must
carry its own exact protected policy scope.

There is intentionally no universal one-hour limit or blanket “never
proactive” rule. Outdoor policies can legitimately use `presence=any`; explicit
owner work bypasses automation limits. No concrete proactive Reolink policy is
armed merely by deploying V2—its camera, trigger, action, destination, and
cadence still need an owner-approved standing definition.

## Protected runtime state

```text
~/.openclaw/reolink-camera/                0700
├── config.json                            0600
└── credentials.json                       0600

~/.openclaw/media/reolink-camera-requests/ 0700, normally empty
~/.openclaw/.secrets-cache                 0600
```

`config.json` contains exact aliases/sites plus private Hub coordinates,
channels, and certificate pins. `credentials.json` contains the approved local
Hub credential. The Cabin Home Hub Mini rejects local `AddUser` with Reolink
code `-9`, so the two current bindings use the explicitly approved
administrator account stored only in this protected registry.

The binding and credential registries use one generation and must contain the
same exact alias set. The helper rejects insecure ownership, permissions,
symlinks, hard links, malformed schemas, unknown fields, unsafe endpoints, and
certificate mismatches.

The helper reads only the exact numeric `DYLAN_CHAT_ID`, `JULIA_CHAT_ID`, or
`HOUSEHOLD_CHAT_ID` assignment needed for a `share` call. It does not source or
execute the secrets cache and never returns or logs a route.

## Enrollment

`reolink-camera-enroll` remains attended and operator-only. It is not in the
skill tool surface and accepts no address, username, password, channel, or
alias on the command line. It:

1. prompts for the exact private endpoint and binding;
2. shows the Hub certificate fingerprint for physical-Hub confirmation;
3. requires exact `TRUST`;
4. reads the password without echo;
5. verifies login, exact-channel status, and one in-memory fresh still; and
6. atomically activates generation-matched owner-only registries.

Enrollment does not change users, camera settings, firmware, protocols, or
network exposure. The optional Reolink macOS Client remains an attended setup
and troubleshooting tool, not an OpenClaw dependency or automation surface.

## Deployment and verification

Tracked components are:

```text
openclaw/skills/reolink-camera/
openclaw/bin/reolink-camera
openclaw/bin/reolink-camera-enroll
openclaw/REOLINK-CAMERA.md
openclaw/tests/test_reolink_camera.py
```

The dotfiles installer deploys the skill as a real runtime copy and installs
the thin wrappers without touching the protected registries. The gateway can
hot-reload the skill; there is no Reolink daemon to restart.

Targeted offline verification:

```bash
python3 -m py_compile openclaw/skills/reolink-camera/reolink-camera
python3 -m unittest openclaw.tests.test_reolink_camera
```

Live verification should cover both camera aliases:

1. status returns safe power data;
2. spotlight status returns the current state;
3. a fresh capture can be described and then cleaned;
4. a same-state spotlight write receives Hub success and verifies without
   altering persistent mode/brightness/schedule; and
5. the media directory is empty afterward.

Do not run a live Dylan/Julia/household delivery canary unless an owner wants an
actual test message.

## Current limits

V2 does not expose live/continuous video, clips, playback, historical
recordings, event downloads, audio/talk, PTZ, zoom, recording or notification
settings, firmware, discovery, account/user management, or arbitrary API
commands.

Siren is intentionally not claimed. Current Reolink Hub support routes siren
control through the proprietary Baichuan transport rather than the pinned HTTPS
surface used here. Add it only after separately proving that transport against
this exact Hub and designing a bounded command.

Capture and spotlight work can wake an Atlas battery camera. The operation is
allowed when authorized, and the camera manages its own return to standby.

## References

- [Reolink spotlight settings](https://support.reolink.com/articles/13291201057817-How-to-Configure-Spotlight-Settings-for-Your-Camera/)
- [Reolink battery status](https://support.reolink.com/articles/34886140030745-Introduction-to-Battery-Status/)
- [CGI, RTSP, and ONVIF support](https://support.reolink.com/articles/900000617826-Which-Reolink-Products-Support-CGI-RTSP-ONVIF/)
- [Reolink software and manuals](https://reolink.com/gb/software-and-manual/)
- [Home Hub setup](https://support.reolink.com/articles/30166537802777-How-to-Initially-Setup-Reolink-Home-Hub-Series-via-Reolink-Software/)
