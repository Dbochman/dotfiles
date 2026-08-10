---
name: petlibro
description: Inspect and safely control location-specific Petlibro feeders and fountains at Crosstown or Cabin. Use for Petlibro device status, feeding schedules, water intake, or an explicit request to dispense a bounded amount of food. Require an exact location and device selector for every device-specific command. Do not use for Litter-Robot devices.
allowed-tools: Bash(petlibro:*)
metadata: {"openclaw":{"emoji":"🐱","requires":{"bins":["petlibro"]}}}
---

# Petlibro Pet Device Control

Use the `petlibro` CLI for Petlibro cloud devices. Treat manual feeding as a
physical, state-changing action: require the user to specify the location and
portions, then report the recorded request ID.

## Exact selectors

Use only these complete selectors; aliases and fuzzy names are rejected:

- `crosstown-feeder`
- `crosstown-fountain`
- `cabin-feeder`
- `cabin-fountain`

Each selector maps through a machine-local configuration entry to one exact
Petlibro device name or serial. The command fails closed if a mapping is
missing, ambiguous, offline, or resolves to the wrong device type. Never pick
the first feeder or fountain returned by the API.

## Read-only commands

```bash
petlibro status
petlibro --json status
petlibro devices
petlibro water crosstown-fountain
petlibro schedule crosstown-feeder
```

`status` and `devices` label configured devices with their exact selectors and
show unconfigured devices as `unmapped`.

## Manual feeding

```bash
petlibro feed crosstown-feeder 1
petlibro feed cabin-feeder 2
```

Before feeding:

1. Require an explicit user request naming Crosstown or Cabin.
2. Require an integer portion count. The default allowed range is 1–3; do not
   split or repeat a request to evade the limit.
3. Do not substitute another feeder when the selected feeder is missing or
   offline.
4. Run the command once and report its request ID.

The CLI writes a protected attempt record before contacting the manual-feed
endpoint and holds an exclusive lock through the outcome. A repeated request
for the same feeder is rejected during the default five-minute cooldown,
including when the earlier network outcome was uncertain. There is no agent
override: wait for the cooldown and physically reconcile the feeder before a
new request.

If the result contains `feed_outcome_unknown`, food may have dispensed. Never
retry automatically.

## Local configuration

Keep credentials and exact device mappings in
`~/.config/petlibro/config.yaml` on the Mac Mini:

```yaml
email: <account-email>
password: <account-password>
device_crosstown_feeder: <exact-device-name-or-serial>
device_crosstown_fountain: <exact-device-name-or-serial>
device_cabin_feeder: <exact-device-name-or-serial>
device_cabin_fountain: <exact-device-name-or-serial>
```

Only mappings for installed devices are required. `PETLIBRO_APPSN` remains in
the protected OpenClaw secret environment. Never display configuration values,
authentication responses, or token-cache contents.

The configuration must be a regular non-symlink file owned by the current user
with mode `0600`; unsafe ownership, type, or permissions fail closed.

The token cache is replaced atomically with mode `0600` only after successful
authentication. Authentication, HTTP, network, malformed JSON, and API errors
return structured nonzero failures without deleting the previous cache.

## Safety boundaries

- Do not construct arbitrary Petlibro API requests; no raw API command exists.
- Do not feed an offline, unmapped, ambiguous, or non-feeder device.
- Do not exceed the configured safe portion range, which cannot be raised
  above three portions.
- Do not bypass the cooldown by changing names, retrying after a timeout, or
  calling the Python implementation directly.
- Treat `feed_outcome_unknown` and `feed_cooldown` as non-retryable.

## Troubleshooting

- `device_mapping_missing`: add the exact selector mapping locally.
- `device_not_found`: inspect `petlibro devices`; do not guess another device.
- `device_offline`: verify power and connectivity at the named location.
- `environment_missing`: refresh the protected OpenClaw secret cache.
- `config_unsafe`: repair the local config ownership/type and set mode `0600`.
- `auth_failed`: verify the secondary Petlibro account credentials locally.
- `network_error`, `http_error`, or `invalid_response`: preserve state and
  retry read-only commands later; never retry an uncertain feed.
