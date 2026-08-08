# Midea AC integration

## Current state

The Cabin LAN currently exposes two supported Midea AC devices. Both use the
V3 protocol and therefore require a device-specific token/key. Anonymous LAN
discovery succeeds; the public default V3 key is rejected as expected.

Both are enrolled and return fresh local status under their MSmartHome names:

- `cabin-air-conditioner`
- `cabin-lil-air-conditioner`

The tracked OpenClaw integration provides:

- exact-alias status and controls over the local network;
- power, mode, setpoint, room/outdoor temperature, humidity, fan, swing,
  comfort features, energy telemetry, and error-code reporting when supported;
- a dedicated locked Python environment pinned to one upstream Git revision;
- an attended enrollment helper that uses the Midea account only to retrieve
  and verify local V3 credentials; and
- owner-only local binding storage with no saved cloud username or password.

## Attended enrollment

Use the account for the app that enrolled the units. `SmartHome` is the default
for MSmartHome; pass another `midea-local` cloud name only when the units were
enrolled in that app.

Inject the username and password from exact 1Password fields into one attended
terminal command without printing either value. First inspect safe candidates:

```bash
MIDEA_USERNAME="$username" MIDEA_PASSWORD="$password" \
  ~/.openclaw/bin/midea-ac-enroll inspect --cloud SmartHome --json
```

If the app names already produce unique, correct room aliases, enrollment can
derive `cabin-<app-name>` automatically:

```bash
MIDEA_USERNAME="$username" MIDEA_PASSWORD="$password" \
  ~/.openclaw/bin/midea-ac-enroll enroll --cloud SmartHome --site cabin \
  --expect-count 2 --json
```

Otherwise bind every displayed candidate explicitly in one call:

```bash
MIDEA_USERNAME="$username" MIDEA_PASSWORD="$password" \
  ~/.openclaw/bin/midea-ac-enroll enroll --cloud SmartHome --site cabin \
  --expect-count 2 \
  --map candidate-1=cabin-<room-one> \
  --map candidate-2=cabin-<room-two> --json
```

Unset the temporary shell variables when the command completes. Enrollment
writes `~/.openclaw/midea-ac/bindings.json` only after every selected binding
authenticates and returns fresh status.

## Event-bus decision

Do not ingest continuous climate or power telemetry. It is high-volume state,
not occupancy evidence, and belongs in direct status/dashboard views.

After exact aliases are enrolled and routine polling has soaked cleanly, a
separate shadow-only observer may be worthwhile for these transitions:

- unit unavailable after bounded consecutive failures, then recovered;
- error code changing from zero to nonzero, then clearing; and
- optionally power/mode changes when the audit value proves useful.

That observer must baseline silently, debounce failures, retain no raw network
or device identifiers, never influence canonical presence, and have no control
path. Activation should be a separate decision after reviewing real device
behavior.
