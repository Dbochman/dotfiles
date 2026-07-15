---
name: fix-clawbody-openclaw-compatibility
description: Repair ClawBody on a Wireless Reachy Mini when it cannot connect to a current OpenClaw gateway or OpenAI Realtime API. Use for `protocol mismatch`, `control ui requires device identity`, `beta_api_shape_disabled`, an app stuck reconnecting to Realtime, or a robot on a routed network that must reach a loopback-only gateway securely.
---

# Fix ClawBody OpenClaw Compatibility

Keep the OpenClaw gateway loopback-only, use an authenticated tunnel when the robot is remote, and patch ClawBody only after reproducing the specific handshake failure. Upstream may incorporate these fixes, so inspect the installed source before editing.

## Establish the network path

1. Confirm Reachy's daemon is healthy at `http://<robot>:8000/api/daemon/status`.
2. Prefer a reverse SSH tunnel from the gateway host to Reachy when the gateway host can route to the robot:

   ```bash
   ssh -NT -o BatchMode=yes -o ExitOnForwardFailure=yes \
     -R 127.0.0.1:18789:127.0.0.1:18789 pollen@<robot>
   ```

3. Configure ClawBody with `OPENCLAW_GATEWAY_URL=ws://127.0.0.1:18789` and the gateway token. Store `.env` as mode `0600`.
4. Make the tunnel persistent with the host's service manager. Use keepalive options and a dedicated SSH key; never commit the key or tokens.

This preserves `gateway.bind: "loopback"` and exposes the forwarded port only on Reachy's loopback interface.

## Repair the OpenClaw handshake

Run a bridge-only connection test before starting the robot app. Apply each repair only when its matching error appears.

### `protocol mismatch`

ClawBody commit `37852552f9e6045867cceee6eb05711311831a17` hard-codes protocol 3. OpenClaw `2026.6.10` requires protocol 4. Confirm the gateway's expected version from its installed message handler or error details, then set ClawBody's `PROTOCOL_VERSION` to that value.

### `control ui requires device identity`

ClawBody incorrectly identifies its programmatic client as the browser Control UI. In `openclaw_bridge.py`:

- set client ID to `gateway-client`;
- set client mode to `backend`;
- request only `operator.read` and `operator.write`;
- remove the synthetic WebSocket `Origin` header.

Keep token authentication. Do not disable OpenClaw device-auth or origin security globally to accommodate this client bug.

## Repair the OpenAI Realtime handshake

For `invalid_request_error.beta_api_shape_disabled`, migrate `openai_realtime.py` from the beta to the GA SDK shape:

- use `client.realtime.connect(...)`, not `client.beta.realtime.connect(...)`;
- set session `type` to `realtime` and `output_modalities` to `["audio"]`;
- nest input/output formats, transcription, voice, and VAD below `audio`;
- use 24 kHz PCM: `{"type": "audio/pcm", "rate": 24000}`;
- rename response events to `response.output_audio.delta` and `response.output_audio_transcript.{delta,done}`.

Use a current Realtime model alias such as `gpt-realtime`. The GA session configuration has this shape:

```python
await conn.session.update(session={
    "type": "realtime",
    "model": model,
    "output_modalities": ["audio"],
    "instructions": "Render only explicitly supplied OpenClaw text verbatim.",
    "audio": {
        "input": {
            "format": {"type": "audio/pcm", "rate": 24000},
            "transcription": {"model": "whisper-1"},
            "turn_detection": {
                "type": "server_vad",
                "create_response": False,
                "interrupt_response": False,
            },
        },
        "output": {
            "format": {"type": "audio/pcm", "rate": 24000},
            "voice": "cedar",
        },
    },
    "tools": [],
    "tool_choice": "none",
})
```

## Make OpenClaw own every voice turn

Do not load identity or memory into Realtime and do not expose robot or OpenClaw
tools there. Realtime is a speech transport, not a second agent:

1. Configure server VAD with `create_response: false` and
   `interrupt_response: false` so speech events and transcription continue without
   an automatic assistant response.
2. On `conversation.item.input_audio_transcription.completed`, send the transcript
   to OpenClaw using the exact session key `agent:main:reachy`.
3. Let OpenClaw load its canonical workspace files and use its normal skills,
   memories, and tools. Add one narrow Dylan-equivalent full-action exception for
   this exact authenticated physical session; retain messaging policy elsewhere.
4. Render only OpenClaw's final text through the dedicated OpenAI Speech endpoint
   with PCM output. Do not send the text to a conversational Realtime response:
   even an out-of-band response can interpret it as a new user turn and add words.
5. Add an owner-only control-socket `speak` command for proactive speech from other
   OpenClaw sessions. Do not call it inside `agent:main:reachy`, whose final response
   is already rendered automatically.
6. Suppress microphone forwarding for the generated PCM duration plus a short tail;
   otherwise Reachy's speaker can trigger VAD and feed its own words back to OpenClaw.

This leaves one source of truth for personality and memory and prevents Realtime
from competing with OpenClaw for a turn or altering OpenClaw's final response.

Keep a distinct processing pose active from completed transcription until the TTS
request has returned playable audio. A fixed upward/side tilt with asymmetric antenna
motion reads more clearly than a subtle idle sway and tells the user not to interrupt.

## Enable face tracking on Reachy Mini Wireless

Reachy Mini Wireless 1.9+ provides daemon-side tracking through
`ReachyMini.start_head_tracking(weight=...)`. Prefer this over installing MediaPipe:
the available ARM MediaPipe build requires NumPy 1.x, while Reachy Mini 1.9 requires
NumPy 2.x. Arm daemon tracking at a subtle idle weight such as `0.25`. On Realtime
`speech_started`, set a weight around `0.85`; on `speech_stopped`, immediately
return it to `0.25`. This makes Reachy look directly at the speaker while listening
and retain gentle awareness without overpowering OpenClaw movement after the
utterance ends. Call `stop_head_tracking()` during app cleanup.

Set `ENABLE_FACE_TRACKING=true` and use a dedicated tracker type such as
`HEAD_TRACKER_TYPE=daemon` so ClawBody does not also initialize YOLO or MediaPipe.
Restarting the ClawBody app is sufficient; a full robot reboot is not required.
Verify both `Speech-gated face tracking active` and `released` in the app log.

## Provide dependency-free dances

Do not report an emotion fallback as a successful dance. The optional
`reachy_mini_dances_library` may be absent, and names such as `wave`, `nod`, and
`bounce` are not emotion names. Provide built-in `Move` implementations for every
advertised dance, keep daemon tracking at no more than idle weight `0.25`, and
include `backend: "builtin"` in a successful result.

When Reachy Mini 1.8.4+ is installed, load the official datasets through
`RecordedMoves`: `pollen-robotics/reachy-mini-dances-library` and
`pollen-robotics/reachy-mini-emotions-library`. Expose the complete live catalog,
not a short enum. Emotion presets have synchronized audio sidecars; start the sound
when queueing the move and suppress microphone forwarding for the move duration.
The official dance dataset is motion-only, while `dance1` through `dance3` in the
emotion dataset are vocalized dance presets.

## Verify

Require all of the following:

1. The gateway still listens only on `127.0.0.1:18789`/`::1:18789`.
2. Reachy's `curl http://127.0.0.1:18789/health` returns a live response through the tunnel.
3. A bridge-only authenticated `chat.send` returns a known marker without error.
4. Reachy's app status reports ClawBody `running`.
5. Daemon logs contain `OpenClaw gateway connected` and report Realtime as speech transport with automatic responses disabled and zero tools.
6. `.env` remains owned by the Reachy user and mode `0600`.
7. A direct voice turn appears in the exact OpenClaw session and only OpenClaw's final text is spoken.
8. Face tracking activates on speech, releases after speech, and a built-in dance then moves visibly.

## References

- https://github.com/tomrikert/clawbody
- https://docs.openclaw.ai/gateway/configuration-reference
- https://github.com/openai/openai-python#realtime-api
- https://developers.openai.com/api/docs/models/gpt-realtime
- https://huggingface.co/docs/reachy_mini/en/SDK/quickstart
