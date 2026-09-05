---
name: petlibro
description: Inspect and safely control location-specific Petlibro feeders and fountains at Crosstown or Cabin. Use for Petlibro device status, feeding schedules, pausing or resuming all scheduled meals, water intake, or an explicit request to dispense a bounded amount of food. Require an exact location and device selector for every device-specific command. Do not use for Litter-Robot devices.
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
petlibro --json schedule-state crosstown-feeder
petlibro --json feeding-history crosstown-feeder 14
```

`status` and `devices` label configured devices with their exact selectors and
show unconfigured devices as `unmapped`. For each online mapped feeder,
`status` also reads and reports whether its full feeding schedule is enabled.
`schedule-state` is the sanitized automation readback for one exact feeder. It
returns only online state, full-schedule enablement, the effective active-meal
count, site, selector, and observation time; it omits meal times, portions,
names, IDs, and raw plans. A paused master schedule reports zero active meals
without requesting the provider meal list, which Petlibro rejects while the
master switch is off.

`feeding-history` returns a bounded, sanitized list of provider-confirmed
scheduled-plan successes for one exact feeder. Each entry contains only its
UTC occurrence time and actual portion count; device identifiers, plan IDs,
raw event text, and manual-feed events are omitted.

## Scheduled feeding

```bash
petlibro schedule-set crosstown-feeder off
petlibro schedule-set cabin-feeder on
petlibro schedule-portions-set crosstown-feeder 4
petlibro schedule-portions-set crosstown-feeder 23:30 4
```

`schedule-set` pauses or resumes the feeder's entire saved schedule. It does
not dispense food, delete individual meals, or prevent a separate manual-feed
request. Require an exact feeder selector and the literal state `on` or `off`.

The command holds an exclusive lock, reads the current state first, writes a
protected audit record before a needed mutation, sends at most one update, and
then verifies the state through a fresh read. A request for an already-matching
state succeeds without a mutation. If the result contains
`schedule_outcome_unknown`, never retry automatically; inspect `petlibro
status` before a new, explicit change.

`schedule-portions-set` changes the portion count of one saved meal without
changing its time, repeat days, label, sound, enabled state, or the feeder's
master schedule switch. Omit `HH:MM` only when the feeder has exactly one saved
meal; otherwise provide the meal's exact 24-hour time. It rejects zero or
ambiguous matches instead of guessing which plan to edit, permits 1–48
scheduled portions, holds the same exclusive schedule lock, records the
attempt before one mutation, and verifies the saved plan through a fresh read.
If the result contains
`schedule_portions_outcome_unknown`, do not retry until a read-only `schedule`
check reconciles the current portion count.

The home-event worker may suspend and later restore an exact full schedule only
under the disabled-by-default cat-transfer policy. It restores only a schedule
recorded as previously suspended by that worker. A manual pause remains paused,
and no automation path imports or invokes `petlibro feed`.

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
- Change scheduled portions only through `schedule-portions-set`, selecting an
  exact time whenever the feeder has more than one saved meal.
- Do not feed an offline, unmapped, ambiguous, or non-feeder device.
- Do not exceed the configured safe portion range, which cannot be raised
  above three portions.
- Do not bypass the cooldown by changing names, retrying after a timeout, or
  calling the Python implementation directly.
- Treat `feed_outcome_unknown` and `feed_cooldown` as non-retryable.
- Treat `schedule_outcome_unknown` as non-retryable until a fresh status read
  reconciles the feeder's actual schedule state.

## Troubleshooting

- `device_mapping_missing`: add the exact selector mapping locally.
- `device_not_found`: inspect `petlibro devices`; do not guess another device.
- `device_offline`: verify power and connectivity at the named location.
- `environment_missing`: refresh the protected OpenClaw secret cache.
- `config_unsafe`: repair the local config ownership/type and set mode `0600`.
- `auth_failed`: verify the secondary Petlibro account credentials locally.
- `invalid_schedule_state`: use only `on` or `off` with `schedule-set`.
- `invalid_scheduled_portions`: use 1–48 with `schedule-portions-set`.
- `schedule_plan_ambiguous`: edit multiple plans in the Petlibro app rather
  than guessing which meal to change.
- `schedule_portions_outcome_unknown`: inspect `schedule`; do not retry until
  the current saved portion count is known.
- `schedule_outcome_unknown`: inspect status; do not repeat the toggle.
- `network_error`, `http_error`, or `invalid_response`: preserve state and
  retry read-only commands later; never retry an uncertain feed.
