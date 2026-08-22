# Home events

Home events is the Mac Mini's private, durable journal for normalized household
activity. Ring, canonical presence, a read-only August observer, a metadata-only
Nest bridge, and a local network-presence adapter publish small site-scoped
records; one ingester serially commits them to SQLite. Producers remain
journal-only. During the Dylan-only canary, the correlator may reserve one
policy-scoped fixed-template message after confirmed vacancy and the arrival
grace; only the separate delivery worker can send it. The bus cannot capture a
camera image, change presence, or operate a lock unless the separately gated
camera-evidence policy is active. That policy permits only the dedicated
camera worker to reduce exact, short-lived +30/+60 stills to a fixed structured
result; it never changes incident or delivery eligibility.

One separately authorized consumer is an explicit exception to that shadow
boundary. The Cabin entry verifier requires ordered live Ring evidence at the
driveway and then the front door within five minutes. Only that complete
sequence may schedule short-lived exact Kitchen stills at +30 and +60 seconds
from the front-door event. It retains only a structured person-visible result,
deletes both frames, and may send Dylan one fixed positive confirmation.

The Nest listener and Cabin reviewer retain their own acknowledgement,
capture, and delivery path. The bridge only mirrors already-committed Nest
person/motion metadata into this journal; it never copies an image, model
observation, message body, camera resource, or provider payload. Ring dog-walk
behavior, the direct doorbell message, vacancy actions, and the August
approval/mutation workflow also remain independent.

The current installed production flags enable canonical presence, the Nest
bridge, the Ring tee, the read-only August observer, and Cabin plus Crosstown
local-presence enrichment. Ring and August were promoted together into shadow
observation on July 23 after an explicit operator decision to accelerate their
otherwise independent gates. August's exact protected binding passed a
read-only status check and its first poll created a silent baseline with no
event. Ring uses exact bindings for both front doors and the Cabin driveway
camera; its legacy paths remain authoritative while the remaining attended
parity checks are pending. The local adapter baselined both enabled sites with
zero events and treated each repeated scan as a no-op. The tracked plist
defaults remain disabled so a fresh install cannot silently activate any
producer.

An observation-only vacancy-action journal was approved and installed on
`2026-08-15`. It wraps only actions already owned by `vacancy-actions.sh`,
records protected bounded intent and outcomes, and fails open without changing
legacy household behavior. It is not yet a bus producer: no new source flag,
schema migration, adapter, correlation rule, reservation, action worker,
notification, or recipient route is active. Publication and every physical
action handoff remain later attended gates.

The Dylan-only canary is now on SQLite schema v5. Its separate camera policy
was activated at `2026-08-05T16:34:03Z` after exact `Kitchen` and
`Living Room Wired` probes, a protected schema backup, full tests, and a
shadow-first migration. Schema v5 was activated at `2026-08-07T22:40:59Z`
after another protected backup and shadow-first migration. The first active
projection was healthy with no queue,
reservation, camera evaluation, delivery outcome, dead letter, or retained
image.

Policy schema 3 added exact Ring evidence at `2026-08-08T02:27:42Z`. Cabin
allows Ring `driveway`/`front_door` plus Nest `Kitchen`; Crosstown allows Ring
`front_door` plus Nest `Living Room Wired`. A non-viewing Cabin front-door
probe succeeded and was deleted. Dormant Cabin driveway and Crosstown
front-door probes failed safely and remain organic-event validation items;
Nest fallback and partial-provider health remain active.

Cabin production schema v2 has exact controller and Kitchen-mesh bindings for
both residents. After two clean scheduled ticks, 12/12 live observations, and
the full test suite, the operator explicitly waived the remaining two canary
ticks and approved the exact installed scanner hash. Presence consumers,
physical actions, and the camera reviewer are restored.

## Data flow

```text
Ring FCM callback -> bounded worker -----------\
Canonical presence evaluation -> source outbox +--> protected source spools
August `observe` over the existing MBP wrapper /               |
Nest listener SQLite -> metadata bridge -------/               |
Sanitized site scans -> local presence adapter -/               |
                                                               v
                                                    single SQLite ingester
                                                               |
                                         +---------------------+------------------+
                                         |                                        |
                                  mode-aware correlator               ordered Cabin verifier
                                      /      \                           driveway -> front door
                         read-only CLI   durable owner delivery               |
                         and `home-events`   Dylan-only fixed sender          |
```

Producers accept at-least-once delivery. A spool record becomes durable only
after its mode-`0600` file and parent directory are fsynced. Ingestion uses a
single process lock, WAL, `BEGIN IMMEDIATE`, foreign keys, a 15-second busy
timeout, and `synchronous=FULL`. A crash after commit but before spool cleanup
is harmless because the replayed event hits an HMAC-derived unique key.

Only safe aliases, sites, normalized event types, times, and bounded
allowlisted attributes are retained. Provider payloads and IDs, account or
network identifiers, coordinates, credentials, recipient identifiers, message
text, camera resources, and media paths are forbidden from the spool,
database, status projection, and logs. The database may retain only the fixed
safe `dylan` policy-route alias; the protected `chat_id` never enters it.

## Tracked components

- `bin/home_event_bus.py` owns validation, the spool, SQLite schema,
  single-writer ingestion, retention, safe status, and read queries.
- `bin/home-eventctl` is the operator-only wrapper. It can initialize, enqueue,
  ingest, inspect, and prune.
- `bin/home-events` is the fixed-root, read-only agent wrapper.
- `bin/home-event-correlator.py` claims durable consumer rows, groups them into
  persistent site incidents, and records shadow decisions or policy-scoped
  reservations. It never performs an external send or camera action.
- `bin/home-event-delivery.py` is the separate fixed-template Dylan-only
  sender. It rechecks fresh canonical vacancy immediately before a single
  attempt and records sent, burned, unknown, or dead-letter outcomes without
  retaining message text or a receipt identifier.
- `bin/home-event-delivery-wrapper.sh` resolves only the protected owner route
  and gateway authentication into a sanitized one-shot process with a bounded
  owner-only log.
- `bin/home-event-camera.py` claims only fresh, confirmed-vacant camera
  evaluations already scheduled by the correlator. It rechecks the protected
  canonical presence state before each +30/+60 slot, after claiming a due
  slot, and immediately before every provider capture. It completes the
  evaluation without further capture when the site is no longer confidently
  vacant.
  Otherwise it combines the exact triggering Ring camera (or the site's Ring
  front door)
  with the exact Nest interior camera, makes only an aggregate person-visible
  decision, deletes every frame immediately, and retains no model prose or
  media path.
- `bin/home-event-camera-wrapper.sh` supplies protected gateway authentication
  to that one-shot worker in a sanitized environment. Camera and delivery
  share the rollback lock, so mode rollback cannot race a capture or send.
- `bin/cabin-entry-verifier.py` is a separate future-only consumer. It requires
  Cabin `driveway` activity followed by `front_door` activity within five
  minutes, gates the sequence on confirmed vacancy, captures exact Kitchen
  stills at +30/+60 seconds from the front-door event, retains only strict
  person-visible decisions, and deletes the frames. Its one-shot attended
  canary may bypass vacancy for the next complete sequence only.
- `bin/cabin-entry-verifier-wrapper.sh` supplies a sanitized cache-only
  LaunchAgent boundary and one bounded owner-only log. It loads only Dylan's
  validated chat target from the protected cache and never invokes `op`.
- `bin/home-event-service-wrapper.sh` is the sanitized LaunchAgent boundary for
  ingestion, correlation, August polling, Nest bridging, and local network
  enrichment, with one bounded safe log.
- `bin/august-event-adapter.py` polls one protected August binding and publishes
  transitions without exposing the mutation commands.
- `bin/nest-home-event-bridge.py` tails committed Nest listener outbox rows and
  publishes only normalized camera aliases, sites, times, and classifications.
  Its first enabled run baselines the existing outbox instead of replaying
  historical camera activity.
- `bin/presence-local-event-adapter.py` reads only sanitized exact-binding
  booleans and canonical resident assignments to derive named local
  arrivals/departures and household excursion intervals.
- `bin/vacancy-action-journal.py` is a local observation-only source journal
  for the legacy vacancy runner. It validates exact protected presence
  causality, records only allowlisted site/target/action outcomes, and has no
  bus publication or device-control interface.
- `skills/home-events/SKILL.md` constrains OpenClaw to the read-only wrapper and
  delegates only an explicit current-image request to `nest-camera`.
- `ai.openclaw.ring-event-listener` remains the only Ring FCM connection and
  tees normalized events to a dedicated publisher worker. It sends only a
  fresh safe-site `person_motion` datagram to dog-walk automation; bus health
  and publication are not automation dependencies.
- `workspace/scripts/presence-detect.sh` remains the canonical presence writer
  and publishes transitions through its protected source outbox.
- Seven attended-install LaunchAgents schedule ingestion, correlation, bounded
  delivery, bounded camera evidence, the disabled-by-default August observer
  and Nest bridge, and the independently site-gated local-presence adapter.
- The verifier has its own attended-install KeepAlive LaunchAgent. Merely
  deploying its files does not activate it: initialization, future-only
  consumer registration, and bootstrap are separate operator steps.

## Limited-delivery boundary

Schema v5 supports `shadow` and `limited_delivery`, but a mode change alone is
insufficient. `limited_delivery` requires a valid mode-`0600` protected policy
whose `active` field is true. Policy replacement is accepted only while the
runtime is in `shadow` mode. The tracked
`home-event-delivery-policy.json` is the exact active Stage 4 policy and is
installed explicitly with:

```bash
home-eventctl install-delivery-policy \
  < ~/dotfiles/openclaw/home-event-delivery-policy.json
```

The correlator reserves an eligible slot only after fresh canonical vacancy
survives the fixed fifteen-minute arrival grace. The separate sender records its
single attempt before calling OpenClaw's supervised iMessage channel, rechecks
fresh vacancy, and holds the dedicated rollback lock across the send and
receipt transition without blocking event ingestion. It never retries an
ambiguous timeout or receipt. An unknown outcome keeps delivery health and the
operator-attention projection degraded until an explicit operator review
records `received`, `not_received`, or `uncertain`; review never retries or
rewrites the outcome.

The schema-v3 protected policy binds Cabin Ring `driveway` and `front_door`
plus Nest `Kitchen`, and Crosstown Ring `front_door` plus Nest
`Living Room Wired`. While camera evidence is enabled, a fresh person, unlock,
or door-open event at a confidently vacant site may schedule +30/+60 evidence.
An attached Ring trigger selects its exact safe alias; an access- or Nest-only
trigger uses that site's Ring front door. The Nest interior camera is always
the secondary view. Old/backfilled events, generic motion, occupied or
uncertain presence, source health, and local-presence inference never schedule
camera work. A later resident arrival or uncertain/stale canonical state
cancels any already-scheduled evaluation before its next snapshot. Canonical
vacancy is checked again after the worker claims a due slot and immediately
before every provider capture, closing the claim-to-capture arrival race;
cancellation is counted as a healthy fail-closed outcome and retains no image.
The resulting
`person_visible`, `no_person_visible`, `uncertain`,
or `unavailable` value may add one fixed sentence to a later eligible Dylan
message; it cannot create, suppress, accelerate, or delay that message.
Any medium-or-high-confidence person result wins across the exact targets;
every target must be clear for `no_person_visible`. A partial Ring or Nest
failure yields bounded uncertainty and degraded camera health while preserving
the other provider's successful evidence. Every image is deleted immediately.

Rollback is always the first operator action:

```bash
home-eventctl set-mode shadow
```

That change leaves ingestion and correlation running, burns unattempted
reservations, marks an already-claimed reservation `unknown`, and cancels
pending camera evaluations rather than risking replay. Julia routing remains
outside this rollout.

## Protected runtime

```text
~/.openclaw/home-events/                 0700
├── config/                              0700
│   ├── dedupe.key                       0600
│   └── delivery-policy.json             0600
├── spool/                               0700
│   ├── ring/                            0700
│   ├── presence/                        0700
│   ├── august/                          0700
│   └── nest/                            0700
└── state/                               0700
    ├── events.sqlite3                   0600
    ├── delivery.lock                    0600
    ├── camera-images/                   0700 normally empty; frames are 0600
    ├── events.sqlite3-wal/-shm          0600 while SQLite is open
    ├── ingest.lock                      0600
    ├── ring-producer.json               0600 durable safe worker health
    ├── status.json                      0600 best-effort projection
    ├── august-adapter.json              0600 observation + durable poll continuity
    ├── august-adapter.pending.json      0600 only during retry/recovery
    ├── august-adapter.lock              0600
    ├── nest-bridge.json                 0600 outbox cursor + DB identity
    ├── nest-bridge.lock                 0600
    ├── presence-local-adapter.json      0600 safe per-site debounce state
    ├── presence-local-adapter.pending.json
    │                                      0600 only during retry/recovery
    └── presence-local-adapter.lock      0600

~/.openclaw/cabin-entry-verifier/        0700
├── state.sqlite3                        0600 structured schedule/results
├── service.lock                         0600
└── images/                              0700 normally empty; frames are 0600

~/.openclaw/presence/home-events-outbox/ 0700
├── producer-state.json                  0600 normalization checkpoint
├── <hash>.pending.json                  0600 before canonical commit
└── <hash>.ready.json                    0600 awaiting bus acknowledgement
~/.openclaw/logs/home-events.log         0600 bounded safe operations
```

SQLite is authoritative. `status.json` is a privacy-safe operational
projection and must not be used as an acknowledgement boundary. Accepted
metadata is retained for 30 days and dead-letter metadata for 90 days; pending
or leased work is never pruned. The five-second ingester checks a durable
SQLite maintenance marker and normally runs automatic retention at most once
every 24 hours; a missing, invalid, or future marker triggers one immediate
repair prune. Process restarts do not reset the gate. An explicit
`home-eventctl prune` remains a forced maintenance operation and checkpoints
the WAL. The internal maintenance marker is not exposed through safe status.
Status includes bus-observed per-source health and safe failure state, consumer
depth and oldest unfinished time, retention, database size, camera-evaluation
health/counts, unresolved delivery-outcome attention, and a separate
access-attention projection. An access incident that expires without a
matching lock/close remains durable historical evidence and increments
attention without redefining current bus health. The operator-only
`review-access-attention` command records that every currently pending access
expiry was reviewed; it never deletes or rewrites the incident. A source with
no evidence remains `unknown`; this does not claim that its process is running.

The event-bus camera evaluator and Cabin verifier store no provider identifiers,
model prose, image path,
recipient, message body, or receipt. The driveway candidate, front-door match,
two capture outcomes, two strict vision decisions, final result, counters, and
sanitized error codes are the complete durable contract.

## Commands

Operator commands use the production root unless an attended test supplies an
absolute `--root` before the subcommand:

```bash
home-eventctl init
home-eventctl check-config
home-eventctl status
home-eventctl review-access-attention
home-eventctl review-delivery-attention --outcome not_received
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
control. The journal stores no historical images. The event-bus camera worker
is not an image-retrieval interface and never sends a frame. An explicit
trusted-owner request for a current frame uses `nest-camera` under that skill's exact-camera,
authorization, delivery-route, and cleanup rules; general activity questions
must never trigger capture.

## Shadow correlation

The correlator re-reads the protected canonical presence file on every run.
Only a fresh, internally fresh `confirmed_vacant` site is treated as vacant;
occupied, stale, malformed, future-dated, insecure, or ambiguous state fails
to uncertain shadow mode.

Ring activity, Nest person detection, and August unlock/open evidence join one
site-scoped activity incident. Nest motion metadata remains visible in recent
events but is deliberately non-actionable: it neither opens nor extends an
incident and produces no notification decision. A fresh arrival resolves an
activity incident silently. Lock/close evidence resolves it only after every
observed open-door and unlocked-lock condition is cleared; a close event
cannot hide an unlocked lock, and vice versa. Routine activity closes after 20
quiet minutes, while an unresolved access incident becomes
`expired_unresolved` after 24 hours and enters the separate operator-attention
projection. Current bus health remains reserved for active operational
failures. If a site becomes
confirmed vacant while an access incident still records an unlocked lock or
open door, the correlator closes the occupied decision boundary and opens a
fresh vacancy-scoped access incident. A matching lock/close during the new
fifteen-minute grace resolves it silently; otherwise it becomes independently
eligible. August source health and battery transitions use separate incident
categories. All incidents and consumer acknowledgements survive restarts.

After a 15-minute arrival grace period, confirmed-vacant activity records a
`shadowed` notification decision, capped at one such decision per site per
hour. Suppressed, rate-limited, and shadowed reasons are retained separately
from the incident's latest resolution summary and returned by the
`home-events explain` command. The first recorded decision is terminal for
that incident: a later presence change or rate-limit expiry cannot
retroactively create a second classification or outbox row. This is
operational evidence only: the correlator contains no delivery, model, camera,
presence mutation, or lock mutation path.

## Source behavior

### Ring

The dog-walk listener keeps the existing FCM session and all legacy behavior.
The tee defaults off; `HOME_EVENTS_RING_ENABLED=1` enables bus publication
without changing legacy Ring handling. Its callback then performs only a
nonblocking enqueue to a 256-record memory queue.
A daemon worker maps each exact known device to a safe site and alias, then
calls `home-eventctl enqueue --source ring`, retrying one failed idempotent
spool commit before recording terminal publication failure. Exact bindings
cover the Crosstown and Cabin `front_door` devices plus the Cabin `driveway` camera;
`driveway` is bus-only and does not become a legacy departure trigger. Unknown
devices are quarantined rather than assigned a site. Queue overflow or
publication failure increments safe counters without blocking Ring or dog-walk
processing. Delivery, binding, and live-FCM ingress health remain separate in
process; startup reconciles the full current Ring video inventory, including
stickup cameras,
without persisting its identifiers. The protected status remains exact schema
v1 for rollback compatibility, projecting aggregate health and cumulative
counters. Resolved binding defects recover without erasing quarantine history.
Recovering a recent provider-history record marks ingress degraded and restarts
the receiver; only a subsequent true live FCM callback restores ingress health.
History-origin records remain inert backfill even when recovered less than a
minute after occurrence; the bus accepts that explicit provenance instead of
misclassifying or rejecting the event as live.
The watchdog checks the receiver tasks rather than trusting the SDK's outer
`started` flag, and the receiver uses its bounded sequential-error abort so
launchd recovery replaces a dead connection instead of allowing an error loop
to grow indefinitely.
The Ring runtime also selects and pads the first encoded Web Push key and salt
header parameters before the strict `firebase-messaging` decoder runs. This
keeps the upstream cryptographic path intact while preventing parameter tails
or missing padding from killing live ingress.
The callback still performs no disk I/O; the dedicated worker owns the bounded
atomic status write.

The unchanged direct Ring message path uses one bounded request through
OpenClaw's already-supervised native iMessage channel and requires a strict
matching receipt. An invalid protected chat target, timeout, or ambiguous
receipt fails without a retry and cannot block or change Ring publication to
the bus.

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

The tracked strict scanner reads identities only from each host's protected,
site-scoped `~/.openclaw/presence-devices.json`. Cabin schema v1 remains a
controller-only compatibility format. Cabin schema v2 is required when mesh is
monitored: it contains exactly one controller plus one or more exact
`target_id` mesh sources, and each source has its own exact
`captiveClientId` binding for Dylan and Julia. A resident's result is the union
of those source-specific positives, but only after every configured source has
returned and validated successfully.

Controller rows use the strict DHCP/lease/idle predicate:
`dhcpLeaseFound` and `dhcpLeaseActive` must be booleans and both true,
`secondsUntilDhcpLeaseExpires` must be finite and positive, and `noDataIdleS`
must be a non-negative integer no greater than 300. Mesh rows use the exact
node-local binding and require `role: "CLIENT"`, finite non-negative
`associatedTimeS`, finite `signalStrength`, and valid RX and TX statistics.
Starlink's `active` field is diagnostic-only and may be false or absent for a
connected mesh client. A failed source request, missing clients array, or
duplicate selected row invalidates the whole observation. Incomplete liveness
on one successful source is per-person unknown: a strict positive for that
resident on another source wins the union, but without any positive the unknown
fails closed rather than becoming absence.

Crosstown uses exact site-private MACs with fresh inbound ARP reachability.
Names, hostnames, generic iPhone matches, and IP addresses are not
strict-scanner fallbacks; missing, insecure, or malformed bindings fail the
scan closed. Sanitized observations and downstream state expose only resident
booleans and safe aggregate evidence, never client or mesh target IDs, names,
addresses, or raw provider rows.

Current Cabin production has protected schema-v2 exact bindings for the
controller and Kitchen mesh node. Enrollment staging has been cleaned, the
exact v1 backup and identifier-free report are retained, and routine deployment
accepts only the explicitly approved scanner hash. Crosstown's strict JSON
binding and exact scanner hash passed the separate
[exact-source shadow canary](plans/crosstown-strict-presence-canary.md); the
strict runtime is active and its exact legacy predecessor remains in a
protected rollback bundle. Each host accepts only the exact scanner hash that
passed its own canary. Any scanner byte change requires a new per-host
approval; Cabin and Crosstown approvals remain independent.

`presence-local-event-adapter.py` is a separate read-only consumer of those
sanitized scans. It does not modify the scanner or canonical state. A first
observation establishes a silent site-local baseline. A departure is inferred
only after a recent exact positive followed by three distinct successful
negative scans spanning at least 30 minutes; duplicate, stale, failed,
malformed, or incomplete scans do not advance the candidate, and a gap longer
than 25 minutes breaks consecutiveness. The first later exact positive records
a network-observed arrival. Each event carries the resident alias, site, and
bounded observation interval rather than an invented exact transition time.

When every resident canonically assigned to one residence is independently
locally away, the adapter starts one household excursion. The first same-site
return ends it; a canonical move to the other residence closes it with the
distinct `residence_relocated` outcome and never invents an arrival. These
records are journal-only: the correlator acknowledges them without opening,
attaching, resolving, or extending an incident, and they cannot establish
vacancy or trigger camera, lock, delivery, or messaging work.

Cabin and Crosstown have independent disabled-by-default tracked flags. Their
installed flags are both enabled after separate scanner approvals. Crosstown's
July 26 baseline and duplicate pass each produced zero events, left canonical
presence unchanged, and created no incident or delivery side effect.

### August

`august observe` is a distinct, sanitized read-only command. The protected MBP
file `~/.openclaw/august/config.json` must remain mode `0600` and include
exactly one `observeLockId` and the v1 alias `observeAlias: "front_door"`;
the lock ID does not belong in this repository or in logs. Its containing
directory must remain an owner-only, non-symlink `0700` directory. The command
binds that exact lock and returns only the alias, observation time,
independently validated `locked|unlocked|unknown` and
`open|closed|unknown` states, and optional validated battery percentage.
Ambiguous or provider-unsupported DoorSense data is represented as `unknown`;
it is never guessed.

The attended binding must preserve the provider's lock-map key byte-for-byte:
lock IDs are case-sensitive. Never case-normalize the key or select a nested
house, MAC, or account field. Validate the exact candidate with a read-only
status call before atomically replacing the protected config, without printing
the identifier.

`august-event-adapter.py` has no mutation entry point. It is disabled unless
`HOME_EVENTS_AUGUST_ENABLED=1`, baselines the first good observation silently,
and then publishes only lock, door, battery-threshold, unavailable, and
recovery transitions. Normal polling is five minutes with up to 30 seconds of
jitter in either direction. Failures back off through
1, 2, 5, 10, and 15 minutes. Relay transport failures remain visible in the
adapter log but produce an unavailable transition only after at least three
failures spanning 30 minutes. This absorbs the observed routine 14- to
23-minute Crosstown transport gaps. Remote-command, missing, oversized,
malformed, and contract-invalid failures retain the stricter threshold of
three consecutive failures or ten minutes. The existing manual lock and
one-use unlock approval paths are unchanged and still require known,
unambiguous physical state. An `unknown` observation is checkpointed silently;
a lock or door transition is emitted only when the immediately previous and
current values are both known and different.

The protected adapter state uses schema v2 to retain cumulative successful and
failed poll counts plus the latest and maximum gap between successful
observations. A v1 state migrates in memory without inventing historical poll
counts; the next atomic checkpoint writes v2. This makes a quiet period
auditable without publishing heartbeat events or retaining provider data.

The observe boundary discards remote stderr, caps stdout before validation,
and reports only allowlisted stages for relay transport, remote-command,
missing, oversized, malformed, or contract-invalid output. The adapter and bus
independently enforce that closed set; provider messages and arbitrary reason
codes cannot enter status or normalized events. The bus additionally accepts
the small legacy stage set during reader-first deployment or rollback, while
the current adapter rewrites those codes to the new taxonomy.

### Nest

`nest-home-event-bridge.py` is a separate downstream reader of the Nest
listener's committed SQLite outbox. It is disabled unless
`HOME_EVENTS_NEST_ENABLED=1`, so a home-event outage can never delay Google
Pub/Sub acknowledgement or the Cabin reviewer. On first enable it stores the
current maximum outbox row and publishes nothing; only later committed rows
are eligible. The cursor advances only after `home-eventctl enqueue --source
nest` has durably accepted each normalized event.

Known exact camera bindings map to the safe aliases `kitchen`, `living_room`,
and `living_room_wired`. Person and motion rows become
`camera.person_detected` and `camera.motion_detected` with the source
classification and source/observation times. The bridge never opens the
camera, invokes vision, or copies an image, model summary, message, raw SDM
identifier, or resource name. Database replacement or rewind fails closed
instead of silently rebasing an existing cursor. Cursor version 2 uses the
database inode plus its APFS creation time as the persistent identity; the
mount-specific device number is refreshed rather than treated as stable across
a reboot. A legacy cursor is upgraded without advancing it only when the inode
matches, the database predates the cursor, and the schema and outbox watermark
remain continuous.

### Ordered Cabin entry verification

`cabin-entry-verifier.py` registers the dedicated
`cabin_entry_verifier` consumer only during attended activation. Registration
does not backfill existing events, so old Ring history cannot schedule a
capture. Every future bus event is acknowledged after filtering; only fresh
source-time Cabin Ring person/motion records from the exact `driveway` and
`front_door` aliases participate.

A driveway record opens a five-minute candidate window but performs no camera
work. A later front-door record must be newer than that driveway record and
inside the window. Front-door-only, driveway-only, reversed, stale, backfilled,
cross-site, and unknown-alias records are inert. A complete sequence is
coalesced for two minutes and schedules exact `Kitchen / Cabin` live stills
30 and 60 seconds after the front-door source timestamp.

Normal sequences require the strict canonical presence gate to say the Cabin
was confirmed vacant when the front-door record completed the sequence. There
is deliberately no later presence veto: the arriving resident may join the
Cabin network before the stills, which is the condition the verifier is trying
to confirm. `arm-canary` grants only the next complete sequence a short-lived
vacancy bypass for an attended test; either Ring event alone cannot consume
the bypass.

The two images are mode `0600`, never leave the Mini, and are deleted after
analysis even when capture, analysis, or delivery fails. Vision returns only
`person_visible` plus `low|medium|high` confidence. Either medium/high positive
produces `person_visible`; two medium/high negatives produce
`no_person_visible`; every partial, failed, or low-confidence pair is
`uncertain`. Only `person_visible` reserves and attempts one fixed text
notification. A crash after that reservation becomes `unknown` and is never
retried, preventing a duplicate.

## Attended rollout

The bus core, correlator, skill, adapters, bridge, and LaunchAgents are
installed at schema v5. The Dylan-only Stage 4 canary entered
`limited_delivery` at `2026-08-05T15:42:14Z`; the tracked delivery policy is
active for both residences with exact Ring-plus-Nest camera evidence enabled.
Cabin uses Ring `driveway`/`front_door` plus Nest `Kitchen`; Crosstown uses
Ring `front_door` plus Nest `Living Room Wired`. Canonical presence, Nest
metadata, Ring, August, and
Cabin plus Crosstown local-presence enrichment are enabled in the installed
runtime; the tracked producer defaults remain off. Both sites completed silent
baselines, duplicate-scan no-ops, and organic departure/return evidence.
The Nest listener schema-v3 migration and bridge baseline are complete. The
bridge retains exact schema-v2 compatibility because schema v3 did not change
its outbox contract. A
fresh or rebuilt installation must first:

1. Confirm the target is the Mac Mini user `dbochman` with
   `HOME=/Users/dbochman`.
2. Deploy the bus scripts, source adapters and bridge, skill, and any tracked
   LaunchAgents through the normal dotfiles flow.
3. Back up the protected home-event database, run `home-eventctl init` to apply the
   attended schema-v5 migration, then verify every runtime directory is `0700`
   and every runtime regular file is `0600`.
4. Install the exact protected delivery policy while mode remains `shadow`.
   Its tracked rollout scope is Dylan only, both sites, three high-confidence
   activity classes, fifteen-minute arrival grace, one-hour cooldown, five-minute
   reservation TTL, 30-minute unresolved-access threshold, and exact Cabin
   Ring `driveway`/`front_door` plus Nest `Kitchen` and Crosstown Ring
   `front_door` plus Nest `Living Room Wired` evidence.
5. Run `home-eventctl check-config`, compilation/tests, and `plutil -lint` for
   each installed plist.

Rollout status and remaining work:

5. Cabin [attended enrollment](plans/cabin-starlink-presence-enrollment.md)
   completed July 23. Schema v2 uses exact controller and mesh bindings; the
   operator accepted two clean scheduled ticks plus 12/12 live observations
   and the full test suite, explicitly waived the remaining two ticks, and
   approved the exact installed scanner hash. Protected jobs are restored.
6. Ring and August were promoted concurrently on July 23 under explicit
   operator approval. The August binding and sanitized observe contract were
   verified without printing the provider identifier, its first poll
   baselined with zero events, its next unattended poll completed cleanly, and
   Ring restarted with its legacy listener healthy. Bus backlog, dead letters,
   and outbound attempt count remained zero.
7. Verify one true post-enrollment presence transition is normalized once,
   then correlate a later organic or attended Nest person event against the
   corrected canonical state without changing listener or reviewer behavior.
8. Stage 2 closed August 5. Crosstown attended ding, transport, and restart
   evidence passed; the operator explicitly waived the remaining Cabin press
   because it would block progress for several days.
9. The attended manual August lock/unlock cycle completed across distinct poll
   boundaries on July 26–27. DoorSense remains `unknown`; attempt door
   open/close evidence only if it begins returning a known state, and never
   invent an event from `unknown`. No automated unlock is authorized.
10. Ring and August completed the concurrent Stage 2 soak and remain
    independently reversible.
11. The Stage 3 delivery foundation and LaunchAgent are installed. Stage 4 was
    explicitly activated at `2026-08-05T15:42:14Z` with the protected policy
    active, runtime mode `limited_delivery`, Dylan as the only route, and
    cameras initially disabled. Exact Nest `Kitchen` and `Living Room Wired`
    evidence was activated separately at `2026-08-05T16:34:03Z`; schema-v3
    policy later added the exact Ring `driveway` and `front_door` views. Follow
    the separate
    [event bus promotion plan](plans/event-bus-promotion-plan.md) for canary
    evidence and stop conditions.
12. The local-presence adapter is enabled for Cabin and Crosstown. Both silent
    baselines, duplicate-scan no-ops, and organic departure/return intervals
    are verified; local shadow enrichment is established at both sites.
13. Throughout every gate, verify `home-eventctl status`, source spool depth,
    SQLite health, shadow decisions, and zero outbound delivery attempts.

Routine pulls preserve the installed `HOME_EVENTS_NEST_ENABLED` and
`HOME_EVENTS_AUGUST_ENABLED` values and both installed local-presence site
flags rather than reverting them to source defaults.

Routine dotfiles pulls may update a service only after its plist is already
installed. They must not create runtime secrets, initialize the database,
bootstrap a new LaunchAgent, enable a producer, or leave shadow mode.
They also compare each protected production schema with the tracked source:
on a mismatch, the prior compatible Nest listener/home-event binaries and
loaded jobs are preserved until the attended migration has completed.

This subsystem is local automation and does **not** require an OpenClaw gateway
restart. Restart or kickstart only the affected producer/ingester job during an
attended rollout.

## Rollback and recovery

Disable consumers first, then stop August, Nest, Ring publishing, and presence
publishing independently. Preserve the database, spools, pending source
batches, and checkpoints for diagnosis. Do not delete runtime state or rotate
the dedupe key during rollback.

Stopping the bus does not stop Ring dog-walk processing, canonical presence,
vacancy actions, the Nest listener or Cabin reviewer, camera on-demand access,
or the existing August CLI. If `check-config` reports insecure permissions, an
unknown schema, integrity failure, or unexpected presence recovery state,
leave the affected producer disabled and repair it in an attended session
rather than recreating or guessing state.
