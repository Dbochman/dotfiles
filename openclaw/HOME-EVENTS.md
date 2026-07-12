# Home events

Home events is the Mac Mini's private, durable journal for normalized household
activity. Ring, canonical presence, and a read-only August observer publish
small site-scoped records; one ingester serially commits them to SQLite. The
system is installed in **shadow mode**: it records and exposes evidence but
does not send a message, capture a camera image, change presence, or operate a
lock.

The existing Nest listener, Ring dog-walk behavior, direct doorbell message,
vacancy actions, and August approval/mutation workflow remain independent.

## Data flow

```text
Ring FCM callback -> bounded worker -----------\
Canonical presence evaluation -> source outbox +--> protected source spools
August `observe` over the existing MBP wrapper /               |
                                                               v
                                                    single SQLite ingester
                                                               |
                                                  shadow correlator
                                                               |
                                     read-only CLI and OpenClaw `home-events`
```

Producers accept at-least-once delivery. A spool record becomes durable only
after its mode-`0600` file and parent directory are fsynced. Ingestion uses a
single process lock, WAL, `BEGIN IMMEDIATE`, foreign keys, a 15-second busy
timeout, and `synchronous=FULL`. A crash after commit but before spool cleanup
is harmless because the replayed event hits an HMAC-derived unique key.

Only safe aliases, sites, normalized event types, times, and bounded
allowlisted attributes are retained. Provider payloads and IDs, account or
network identifiers, coordinates, credentials, recipients, message text,
camera resources, and media paths are forbidden from the spool, database,
status projection, and logs.

## Tracked components

- `bin/home_event_bus.py` owns validation, the spool, SQLite schema,
  single-writer ingestion, retention, safe status, and read queries.
- `bin/home-eventctl` is the operator-only wrapper. It can initialize, enqueue,
  ingest, inspect, and prune.
- `bin/home-events` is the fixed-root, read-only agent wrapper.
- `bin/home-event-correlator.py` claims durable consumer rows, groups them into
  persistent site incidents, and records shadow-only policy decisions.
- `bin/home-event-service-wrapper.sh` is the sanitized LaunchAgent boundary for
  ingestion, correlation, and August polling, with one bounded safe log.
- `bin/august-event-adapter.py` polls one protected August binding and publishes
  transitions without exposing the mutation commands.
- `skills/home-events/SKILL.md` constrains OpenClaw to the read-only wrapper and
  delegates only an explicit current-image request to `nest-camera`.
- `skills/dog-walk/dog-walk-listener.py` remains the only Ring FCM connection
  and tees normalized events to a dedicated publisher worker.
- `workspace/scripts/presence-detect.sh` remains the canonical presence writer
  and publishes transitions through its protected source outbox.
- Three attended-install LaunchAgents schedule ingestion, correlation, and the
  disabled-by-default August observer.

## Protected runtime

```text
~/.openclaw/home-events/                 0700
├── config/                              0700
│   └── dedupe.key                       0600
├── spool/                               0700
│   ├── ring/                            0700
│   ├── presence/                        0700
│   └── august/                          0700
└── state/                               0700
    ├── events.sqlite3                   0600
    ├── events.sqlite3-wal/-shm          0600 while SQLite is open
    ├── ingest.lock                      0600
    ├── ring-producer.json               0600 durable safe worker health
    ├── status.json                      0600 best-effort projection
    ├── august-adapter.json              0600 after first poll
    ├── august-adapter.pending.json      0600 only during retry/recovery
    └── august-adapter.lock              0600

~/.openclaw/presence/home-events-outbox/ 0700
├── producer-state.json                  0600 normalization checkpoint
├── <hash>.pending.json                  0600 before canonical commit
└── <hash>.ready.json                    0600 awaiting bus acknowledgement
~/.openclaw/logs/home-events.log         0600 bounded safe operations
```

SQLite is authoritative. `status.json` is a privacy-safe operational
projection and must not be used as an acknowledgement boundary. Accepted
metadata is retained for 30 days and dead-letter metadata for 90 days; pending
or leased work is never pruned. Status includes bus-observed per-source health
and safe failure state, consumer depth and oldest unfinished time, retention,
and database size. A source with no evidence remains `unknown`; this does not
claim that its process is running.

## Commands

Operator commands use the production root unless an attended test supplies an
absolute `--root` before the subcommand:

```bash
home-eventctl init
home-eventctl check-config
home-eventctl status
home-eventctl ingest-once --limit 100
home-eventctl prune
printf '%s\n' '<strict normalized JSON>' | \
  home-eventctl enqueue --source ring
/opt/homebrew/bin/python3 -I \
  "$HOME/.openclaw/bin/home-event-correlator.py" --limit 20
```

`enqueue` is a producer interface, not an interactive event-injection tool.
Do not fabricate household events in the production root for testing.

The agent-facing commands are read-only and always return structured JSON:

```bash
home-events status --json
home-events recent --site cabin --since 2h --limit 20 --json
home-events recent --site crosstown --type door.opened --since 24h --json
home-events incidents --state open --json
home-events incidents --site crosstown --state all --since 24h --json
home-events explain 'inc_<32-lowercase-hex>' --json
```

The agent wrapper has no root override and cannot invoke initialization,
enqueue, ingestion, retention, queue acknowledgement, policy, or adapter
control. The journal stores no historical images. An explicit trusted-owner
request for a current frame uses `nest-camera` under that skill's exact-camera,
authorization, delivery-route, and cleanup rules; general activity questions
must never trigger capture.

## Shadow correlation

The correlator re-reads the protected canonical presence file on every run.
Only a fresh, internally fresh `confirmed_vacant` site is treated as vacant;
occupied, stale, malformed, future-dated, insecure, or ambiguous state fails
to uncertain shadow mode.

Ring activity and August unlock/open evidence join one site-scoped activity
incident. A fresh arrival resolves that incident silently. Lock/close evidence
resolves it only after every observed open-door and unlocked-lock condition is
cleared; a close event cannot hide an unlocked lock, and vice versa. Routine activity closes after 15 quiet minutes,
while an unresolved access incident becomes `expired_unresolved` after 24
hours and degrades the bus health projection for operator review. August
source health and battery transitions use separate incident
categories. All incidents and consumer acknowledgements survive restarts.

After a 90-second arrival grace period, confirmed-vacant activity records a
`shadowed` notification decision, capped at one such decision per site per
hour. Suppressed, rate-limited, and shadowed reasons are retained separately
from the incident's latest resolution summary and returned by the
`home-events explain` command. This is operational evidence only: the correlator contains no delivery,
model, camera, presence mutation, or lock mutation path.

## Source behavior

### Ring

The dog-walk listener keeps the existing FCM session and all legacy behavior.
The tee defaults off; `HOME_EVENTS_RING_ENABLED=1` enables bus publication
without changing legacy Ring handling. Its callback then performs only a
nonblocking enqueue to a 256-record memory queue.
A daemon worker maps the exact known device to the local `front_door` alias,
then calls `home-eventctl enqueue --source ring`. Unknown devices are
quarantined. Queue overflow or publication failure increments safe counters
and marks the protected `ring-producer.json` projection degraded without
blocking Ring or dog-walk processing. The callback still performs no disk I/O;
the dedicated worker owns that bounded atomic status write.

Durability begins at the worker's spool commit, so there is an accepted small
callback-to-worker crash window. Provider IDs exist only in memory and are
keyed and discarded by the bus.

### Presence

Only the Mini publishes normalized presence. Crosstown continues to send its
validated scan through the existing Taildrop receiver. Presence remains the
canonical hard gate in `~/.openclaw/presence/state.json`; the bus never writes
it. A bus failure retains ready work for retry while the successfully committed
canonical evaluation remains authoritative.

With `HOME_EVENTS_PRESENCE_ENABLED=1`, a transition batch carries its prior and
target state hashes. The canonical state file is the commit marker. Before a
new evaluation, recovery either completes a target-matching batch, discards a
prior-matching uncommitted batch for reevaluation, or leaves an unexpected
third-state batch untouched and fails health closed. The first enabled
evaluation baselines silently, and repeated identical evaluations publish
nothing.

### August

`august observe` is a distinct, sanitized read-only command. The protected MBP
file `~/.openclaw/august/config.json` must remain mode `0600` and include
exactly one `observeLockId` and the v1 alias `observeAlias: "front_door"`;
the lock ID does not belong in this repository or in logs. Its containing
directory must remain an owner-only, non-symlink `0700` directory. The command
binds that exact lock and returns only the alias, observation time, unambiguous
lock/door state, and optional validated battery percentage.

`august-event-adapter.py` has no mutation entry point. It is disabled unless
`HOME_EVENTS_AUGUST_ENABLED=1`, baselines the first good observation silently,
and then publishes only lock, door, battery-threshold, unavailable, and
recovery transitions. Normal polling is five minutes with up to 30 seconds of
jitter in either direction. Failures back off through
1, 2, 5, 10, and 15 minutes; three consecutive failures or ten minutes without
a good observation produces one unavailable transition. The existing manual
lock and one-use unlock approval paths are unchanged.

## Attended rollout

The tracked build must remain unloaded and shadow-only until an attended
session:

1. Confirm the target is the Mac Mini user `dbochman` with
   `HOME=/Users/dbochman`.
2. Deploy the three bus scripts, source adapters, skill, and any tracked
   LaunchAgents through the normal dotfiles flow.
3. Run `home-eventctl init`, then verify every runtime directory is `0700` and
   every runtime regular file is `0600`.
4. Run `home-eventctl check-config`, compilation/tests, and `plutil -lint` for
   each installed plist.
5. Configure the protected August observe binding on the MBP without printing
   it, then verify `august observe` returns only the sanitized contract.
6. Enable and baseline presence and Ring one at a time. Presence requires
   changing `HOME_EVENTS_PRESENCE_ENABLED` in both the Cabin evaluator and
   Taildrop receiver plists and reloading them together so every serialized
   evaluation uses the same outbox contract.
7. Soak presence for 48–72 hours with one controlled relocation, and soak Ring
   for at least 48 hours with one attended ding and person-motion test at each
   configured site. Verify legacy Ring/dog-walk output is unchanged before
   proceeding.
8. Run Ring plus presence correlation in shadow for at least seven days. Stop
   if there is a parity gap, unbounded backlog, duplicate/cross-site incident,
   or any outbound delivery attempt.
9. Only after those gates pass, enable August and perform an attended lock,
   unlock, door-open, and door-close cycle. Soak August in shadow for seven
   days before considering any later policy activation. Routine pulls preserve
   the installed `HOME_EVENTS_AUGUST_ENABLED` value rather than reverting it.
10. Throughout every gate, verify `home-eventctl status`, source spool depth,
   SQLite health, shadow decisions, and zero outbound delivery attempts
   throughout the soak.

Routine dotfiles pulls may update a service only after its plist is already
installed. They must not create runtime secrets, initialize the database,
bootstrap a new LaunchAgent, enable a producer, or leave shadow mode.

This subsystem is local automation and does **not** require an OpenClaw gateway
restart. Restart or kickstart only the affected producer/ingester job during an
attended rollout.

## Rollback and recovery

Disable consumers first, then stop August, Ring publishing, and presence
publishing independently. Preserve the database, spools, pending source
batches, and checkpoints for diagnosis. Do not delete runtime state or rotate
the dedupe key during rollback.

Stopping the bus does not stop Ring dog-walk processing, canonical presence,
vacancy actions, Nest monitoring, camera on-demand access, or the existing
August CLI. If `check-config` reports insecure permissions, an unknown schema,
integrity failure, or unexpected presence recovery state, leave the affected
producer disabled and repair it in an attended session rather than recreating
or guessing state.
