# Reachy Mini

This is the canonical description and runbook for the physically secured
Wireless Reachy Mini at Crosstown. It documents the deployed architecture as
verified on July 16, 2026. ClawBody application code lives in the
[`Dbochman/clawbody`](https://github.com/Dbochman/clawbody) fork; this repository
owns the OpenClaw plugin, skills, relay services, and operating policy.

## Current state

| Component | Deployed state |
| --- | --- |
| Reachy Mini | `pollen@192.168.165.129`, Wireless daemon `1.9.0`, hardware motor control enabled |
| ClawBody | `/home/pollen/clawbody`, `main` at `8bb58bc`, installed editable in `/venvs/apps_venv` |
| Crosstown MBP | `dbochman@100.107.209.85` (`192.168.165.111` on the Crosstown LAN), always-on relay and primary robot-control host |
| Mac mini | `dbochman@100.104.114.1`, OpenClaw gateway and agent host |
| Reachy Mini Control | Pollen Robotics `0.9.33`, installed on the Crosstown MBP, normally closed |
| Voice | Direct OpenAI Realtime, `gpt-realtime-2.1-mini`, Cedar voice, 24 kHz PCM |
| Delegation | Exact OpenClaw session `agent:main:reachy`, pinned to `openai/gpt-5.4-mini` |
| Continuity | `reachy-continuity` gateway plugin, exact Reachy/iMessage sessions only |
| Wake word | Disabled; microphone mute is the conversation gate |
| Live status | ClawBody ready, direct voice owns the robot, microphone unmuted at volume 100 |

The OpenClaw global model remains `openai/gpt-5.6-sol`; the smaller Reachy
delegation model is scoped only to `agent:main:reachy`.

## Architecture

Reachy runs its robot daemon, ClawBody, Realtime connection, local tools, audio,
and movement loop on the robot. Ordinary conversation does not start an
OpenClaw model turn. The Realtime model responds directly and delegates only
requests that need OpenClaw skills, current/external data, files, messaging,
home control, bookings, purchases, or durable memory.

```text
                                  OpenAI Realtime API
                                          ^
                                          | TLS, direct voice/audio
                                          |
                                  Reachy Mini / ClawBody
                                  192.168.165.129
                                   |             ^
              ws://127.0.0.1:18789 |             | owner-only Unix socket
                                   |             | via dedicated SSH
                                   v             |
                  reverse SSH on Reachy loopback | 
                                   |             |
                          Crosstown MBP           |
                  127.0.0.1:28789 + reachyctl ----+
                                   |
                           authenticated SSH
                                   |
                                   v
                              Mac mini
                  OpenClaw on 127.0.0.1:18789
```

The private gateway path is two persistent SSH legs on the Crosstown MBP:

1. `ai.openclaw.reachy-gateway-upstream` forwards MBP
   `127.0.0.1:28789` to the Mac mini's `127.0.0.1:18789`.
2. `ai.openclaw.reachy-gateway-relay` reverse-forwards Reachy's
   `127.0.0.1:18789` to MBP `127.0.0.1:28789`.

The gateway therefore remains loopback-only on all three machines. The legacy
Mac-mini-hosted `ai.openclaw.reachy-gateway-tunnel` is stopped and retained only
for rollback. It must not run alongside the MBP relay because both claim
Reachy's loopback port `18789`.

The Crosstown MBP has AC system sleep disabled with `sudo pmset -c sleep 0`.
Display sleep and battery sleep remain enabled.

## Voice and turn ownership

ClawBody's deployed `.env` selects:

```text
REACHY_VOICE_MODE=direct
OPENAI_MODEL=gpt-realtime-2.1-mini
OPENAI_VOICE=cedar
OPENAI_TTS_MODEL=gpt-4o-mini-tts
OPENAI_TTS_VOICE=cedar
OPENAI_TTS_FALLBACK_MODEL=tts-1
OPENAI_TTS_FALLBACK_VOICE=coral
OPENAI_AUDIO_JITTER_MS=220
REACHY_BARGE_IN=true
REACHY_WAKE_WORD_ENABLED=false
CONTINUITY_SUMMARY_MODEL=gpt-5.4-mini
```

Defaults currently used by the code add `gpt-4o-mini-transcribe`, English
transcription, 400 ms server-VAD silence detection, and a two-second continuity
refresh interval. Do not put API keys or gateway tokens in this document.
Reachy's `/home/pollen/clawbody/.env` is the owner-only source of secret runtime
values and must remain mode `0600`.

### Direct turns

- Realtime receives microphone audio and produces speech without invoking the
  OpenClaw gateway for an ordinary turn.
- The first native audio is held behind a 220 ms pre-roll buffer. Subsequent PCM
  is streamed, avoiding the prior first-second underrun without buffering an
  entire response.
- Barge-in is enabled. New speech stops and flushes current native Realtime
  playback and truncates the assistant audio item to what was actually played.
- Proactive `reachyctl speak` inherits `OPENAI_VOICE` and uses the expressive
  OpenAI speech renderer with lively delivery instructions. If that render
  fails, ClawBody retries once with `tts-1`/Coral. Both paths buffer the complete
  clip before playback and suppress echo input for the clip duration plus a
  short tail; command results identify the model, voice, and fallback state.

### Visible turn states

- **Listening:** server VAD reports speech; face tracking rises to weight
  `0.85` and the listening pose takes precedence.
- **Thinking:** speech has stopped but no response audio is ready; Reachy holds
  the processing pose so the user can see that a reply is being prepared.
- **Speaking:** the processing pose releases on the first speaker push and
  speech-driven head motion accompanies the audio.
- **Idle:** face tracking returns to weight `0.25`, leaving deliberate look,
  emotion, and dance commands in control of the head.
- **External control:** `reachyctl` acquires an exclusive lease. Direct voice,
  queued audio, and speech tracking pause until the command finishes.

ClawBody logs transcription-ready, first-direct-audio, and first-speaker-push
latency markers for performance diagnosis.

## Identity, personality, and continuity

The Realtime session is the same OpenClaw identity, not a separate generic
assistant. Through authenticated `reachy.continuity.context` gateway RPC,
ClawBody loads and applies:

1. `IDENTITY.md`
2. `SOUL.md`
3. `USER.md`
4. the current Reachy continuity capsule

ClawBody checks the context revision every two seconds and updates the active
Realtime session only when the identity stack changes. If the gateway is
temporarily unavailable, it continues with the last valid context.

The exact internal session `agent:main:reachy` is authenticated traffic from
the physically secured robot and is treated as Dylan's full-trust embodiment
session. It does not apply messaging-contact verification to the in-person
speaker. Normal trusted-contact and channel authorization rules remain in force
for iMessage and every other messaging route.

The `reachy-continuity` gateway plugin binds only:

- Reachy: `agent:main:reachy`
- Dylan iMessage: `agent:main:imessage:direct:dylanbochman@gmail.com`

It maintains at most 12 compact semantic summaries for four hours and explicit
handoffs for 24 hours. Direct voice and OpenClaw turns are summarized
asynchronously, so continuity does not add response latency. The plugin stores
no raw audio or verbatim room transcript, does not merge session transcripts,
consumes handoffs by exact ID, rejects broader session access, and fails closed.

Reachy-originated durable memory is allowed only when the current turn contains
an explicit imperative such as “remember this,” “save this,” or “write this
down.” Capsule history is context, not current authorization for messages,
purchases, bookings, home control, or other mutations.

## Robot capabilities

The local Realtime tools and the OpenClaw `reachy-control` skill expose:

- directional look and neutral/idle control;
- camera capture when visual context is requested;
- the complete installed official emotion and dance catalogs;
- bundled vocalizations for official emotion presets, including `dance1`,
  `dance2`, and `dance3`;
- motion-only official dances and six dependency-free built-in fallbacks;
- proactive speech;
- movement stop;
- microphone status, mute, and unmute.

Run `reachyctl presets` for the authoritative live catalog. The documented
snapshot is in
[`skills/reachy-control/references/presets.md`](skills/reachy-control/references/presets.md).

OpenClaw may use these controls while working on a delegated request. The direct
voice session speaks the final delegated answer itself; it must not also call
`reachyctl speak` for that answer.

## Microphone and physical privacy controls

`reachyctl mute` and the direct voice microphone tool set the daemon-managed
microphone volume to zero. ClawBody remembers the last nonzero volume in the
running process, defaulting to 100 after restart.

Mute also:

1. cancels listening and thinking states;
2. stops face tracking;
3. clears queued movement;
4. holds a bowed quiet pose with folded antennas;
5. waits one second for the pose to settle;
6. relaxes only the left and right antenna motors.

While muted, moving **both** antennas at least `0.25` radians (about 14 degrees)
for three consecutive 100 ms samples unmutes Reachy. ClawBody queues the neutral
pose, waits 120 ms, and then re-enables antenna torque to avoid a snap back. A
single displaced antenna does not trigger the gesture. An authenticated
`reachyctl unmute` remains the remote recovery path. Reachy's dashboard slider
operates at the daemon volume layer and may not immediately reflect a
ClawBody-initiated change; use `reachyctl status` as the authoritative combined
microphone, pose-watcher, and control state.

The wake-word implementation and bundled `hey_claude.onnx` model remain in
ClawBody, but `Hey Claude` gating is intentionally disabled. If re-enabled, the
configured threshold is `0.5`, with 10 seconds to start the initial command and
a 20-second follow-up window.

## Control path

On the Mac mini, the protected `~/.openclaw/reachy-control-relay` file contains
the exact `dylans-macbook-pro` SSH alias. The installed `reachyctl` validates
the command, relays it to the MBP, and the MBP connects to Reachy with
`~/.ssh/openclaw-reachy`. On the MBP, no relay file is present, so the same CLI
connects directly to Reachy.

The robot-side `/home/pollen/clawbody/bin/clawbody-control` client sends one
constrained JSON request to ClawBody's mode-`0600` Unix socket. No robot-control
TCP port is exposed. Supported commands are:

```bash
reachyctl status
reachyctl presets
reachyctl see
reachyctl look left|right|up|down|front
reachyctl emotion <preset>
reachyctl dance <preset>
reachyctl speak "Text to say aloud"
reachyctl mute
reachyctl unmute
reachyctl stop
reachyctl idle
```

## Reachy Mini Control app

The notarized Pollen Robotics **Reachy Mini Control** app version `0.9.33` is
installed at `/Applications/Reachy Mini Control.app` on the Crosstown MBP. It is
an optional interactive management client and local proxy for Reachy's daemon
UI; it is not part of the production voice, gateway, continuity, movement,
audio, or `reachyctl` path.

Leave the app closed during normal operation. Open it when attended access is
useful for installing or restarting robot apps, viewing daemon logs, changing
robot settings, or troubleshooting. Closing it does not stop ClawBody because
ClawBody and the app manager run on Reachy itself. The dashboard volume slider
may also lag a ClawBody-initiated mute change, so `reachyctl status` remains the
authoritative combined control state.

The app is intentionally not installed on the Mac mini. Adding another copy
there would provide only a redundant cold-spare GUI and would not improve
latency, voice reliability, gateway availability, or robot control. The app may
remain on Dylan's workstation for attended use, but that copy is likewise not
required to stay open.

Verify the MBP installation without launching it:

```bash
ssh dbochman@100.107.209.85 'app="/Applications/Reachy Mini Control.app"; plutil -extract CFBundleShortVersionString raw "$app/Contents/Info.plist"; codesign --verify --deep --strict "$app"; spctl -a -vv -t execute "$app"'
```

## Security boundaries

See [`../docs/ssh-host-access.md`](../docs/ssh-host-access.md) for the exact
host-to-host trust matrix, identity fingerprints, machine-local key locations,
and SSH recovery procedure.

- Keep OpenClaw bound to `127.0.0.1:18789` and `::1:18789`.
- Keep both SSH forwards bound to loopback; do not publish gateway port `18789`
  on the LAN or Tailscale.
- Use `BatchMode`, `IdentityAgent=none`, strict host-key checking, keepalives,
  and the dedicated Reachy identity in the persistent services.
- Keep the Reachy `.env`, control socket, relay selector, and continuity state
  owner-only. Never commit keys, API credentials, or gateway tokens.
- Treat only exact `agent:main:reachy` traffic as the physical owner session.
- Use the camera only when requested or necessary for the current task; do not
  retain or forward incidental room imagery or conversation.
- Let the continuity plugin enforce exact sessions, bounded retention, safe
  parsing, symlink rejection, atomic writes, and explicit memory authorization.

## Source and deployed paths

| Purpose | Canonical source | Deployed location |
| --- | --- | --- |
| ClawBody | `Dbochman/clawbody` | Reachy `/home/pollen/clawbody` |
| Reachy control skill | [`skills/reachy-control`](skills/reachy-control) | Mac mini and MBP `~/.openclaw/skills/reachy-control` |
| Control wrapper | [`bin/reachyctl`](bin/reachyctl) | `~/.openclaw/bin/reachyctl` and normal CLI path |
| Continuity policy | [`skills/reachy-continuity`](skills/reachy-continuity) | Mac mini `~/.openclaw/skills/reachy-continuity` |
| Continuity enforcement | [`plugins/reachy-continuity`](plugins/reachy-continuity) | Mac mini OpenClaw plugin directory |
| MBP upstream | [`launchagents/ai.openclaw.reachy-gateway-upstream.plist`](launchagents/ai.openclaw.reachy-gateway-upstream.plist) | MBP `~/Library/LaunchAgents` |
| MBP reverse relay | [`launchagents/ai.openclaw.reachy-gateway-relay.plist`](launchagents/ai.openclaw.reachy-gateway-relay.plist) | MBP `~/Library/LaunchAgents` |
| Legacy rollback tunnel | [`launchagents/ai.openclaw.reachy-gateway-tunnel.plist.disabled`](launchagents/ai.openclaw.reachy-gateway-tunnel.plist.disabled) | Mac mini `~/Library/LaunchAgents/ai.openclaw.reachy-gateway-tunnel.plist.disabled` |
| Reachy Mini Control | Pollen Robotics notarized application; not repository-managed | MBP `/Applications/Reachy Mini Control.app` (installed, normally closed) |

## Verification

Check both persistent MBP legs:

```bash
ssh dbochman@100.107.209.85 'launchctl print "gui/$(id -u)/ai.openclaw.reachy-gateway-upstream" | grep -E "state =|pid =|last exit code"'
ssh dbochman@100.107.209.85 'launchctl print "gui/$(id -u)/ai.openclaw.reachy-gateway-relay" | grep -E "state =|pid =|last exit code"'
```

Require the upstream and the robot-side gateway health checks to succeed:

```bash
ssh dbochman@100.107.209.85 'curl -fsS http://127.0.0.1:28789/health'
ssh dbochman@100.107.209.85 'ssh -i ~/.ssh/openclaw-reachy -o BatchMode=yes -o IdentityAgent=none pollen@192.168.165.129 "curl -fsS http://127.0.0.1:18789/health"'
```

Check control through both intended entry points:

```bash
ssh dbochman@100.107.209.85 '~/.openclaw/bin/reachyctl status'
ssh dylans-mac-mini 'reachyctl status'
```

Confirm the gateway remains private and the legacy service is stopped:

```bash
ssh dylans-mac-mini 'lsof -nP -iTCP:18789 -sTCP:LISTEN'
ssh dylans-mac-mini 'launchctl print "gui/$(id -u)/ai.openclaw.reachy-gateway-tunnel"'
```

The first command should show only `127.0.0.1:18789` and `[::1]:18789`; the
second should report that the service is not loaded. Inspect continuity runtime
registration with:

```bash
ssh dylans-mac-mini 'openclaw plugins inspect reachy-continuity --runtime --json'
```

For robot logs:

```bash
ssh dbochman@100.107.209.85 'ssh -i ~/.ssh/openclaw-reachy -o BatchMode=yes -o IdentityAgent=none pollen@192.168.165.129 "journalctl -u reachy-mini-daemon.service --since \"15 minutes ago\" --no-pager"'
```

Look for successful gateway/Realtime configuration and absence of repeating
reconnect, audio, torque, or control-socket errors.

## Deployment

### ClawBody

1. Make and test changes in the local `Dbochman/clawbody` fork.
2. Push `main`.
3. From the MBP, fast-forward `/home/pollen/clawbody` on Reachy.
4. Run `/venvs/apps_venv/bin/pip install -e .`.
5. Restart the current app once through Reachy's daemon API.
6. Wait for `reachyctl status`, gateway health, and Realtime logs to recover.

Do not restart twice: a duplicate restart can interrupt the new app during its
startup sequence.

### Dotfiles and runtime configuration

1. Update the canonical plugin, skill, wrapper, LaunchAgent, and this document
   together when their contract changes.
2. Run `plutil -lint` on changed LaunchAgents, `bash -n` on shell wrappers,
   the skill validator, and `./sync.sh validate`.
3. Commit and push `main`.
4. Fast-forward the MBP checkout and deploy only the MBP-specific Reachy
   LaunchAgents there.
5. Deploy the Reachy skill/wrapper to both the MBP and Mac mini without copying
   secrets into the repository.
6. Repeat the verification sequence above.

## Rollback

To restore the legacy gateway tunnel, stop the MBP reverse relay before loading
the Mac mini service:

```bash
ssh dbochman@100.107.209.85 'launchctl bootout "gui/$(id -u)/ai.openclaw.reachy-gateway-relay"'
ssh dylans-mac-mini 'cp ~/Library/LaunchAgents/ai.openclaw.reachy-gateway-tunnel.plist.disabled ~/Library/LaunchAgents/ai.openclaw.reachy-gateway-tunnel.plist && launchctl enable "gui/$(id -u)/ai.openclaw.reachy-gateway-tunnel" && launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/ai.openclaw.reachy-gateway-tunnel.plist'
```

The MBP upstream may remain loaded because it binds only MBP loopback port
`28789`. To restore direct Mac-mini-to-Reachy control, disable the protected
relay selector without deleting it, then verify:

```bash
ssh dylans-mac-mini 'mv ~/.openclaw/reachy-control-relay ~/.openclaw/reachy-control-relay.disabled && reachyctl status'
```

Restore the selector after the MBP path is healthy. Never load both reverse
tunnels simultaneously.
