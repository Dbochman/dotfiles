# Bluetooth bridges

The M5Stack ATOM Lite runs ESPHome's BLE proxy firmware. A client on the
household network receives Bluetooth advertisements and performs GATT reads
through its encrypted ESPHome API. This extends radio reach; each sensor still
needs a compatible protocol reader. It does not create a macOS Bluetooth adapter.

## First device: Crosstown

On November 8, 2026, the original ATOM Lite connected to `dylans-mac` was
identified as an ESP32-PICO-D4 with 4 MB of flash. Its complete factory firmware
was backed up before installing ESPHome 2026.10.2. The write passed the flasher's
hash verification. The bridge's configured name is `crosstown-ble-01`.

Wi-Fi enrollment succeeded through the attended USB dialogs. The encrypted API
reported the expected name and ESPHome version. BLE advertisement delivery was
verified both from Dylan's Mac and from the Mini through a private SSH tunnel
via the always-on `crosstown-mac`. The laptop's Bluetooth radio is not involved.

**Initial restart and power-loss tests succeeded, but later reconnects needed PIN recovery.** The owner entered a fresh
display PIN locally on Dylan's Mac. Secure Connections authentication completed
with bonding (mode 13), and the ATOM reported one stored bond. After a hardware
reset, it still reported one bond, authenticated without another code, and read
protected characteristics through the Crosstown Mac. The dedicated reading
characteristic returned 649 ppm CO₂, exactly matching the owner's display check.
The owner then unplugged the ATOM from the laptop and moved it to a standalone
USB power adapter. It retained one bond, authenticated without another code,
and returned 660 ppm through the Crosstown Mac. The dedicated reading and newest
history record agreed; the reading had advanced from the earlier 649 ppm.
The scheduled collector, cached OpenClaw skill, and climate dashboard integration
are described below.

Earlier active/passive and five-minute scans reported zero candidates because a
local variable shadowed the UUID module inside the advertisement callback.
Callback errors were hidden by the diagnostic's logging configuration. Those
negative results must not be used as evidence of sensor incompatibility. The
probe now reports callback failures explicitly, and fixture checks cover both
UUID byte orders, the owner-reported pairing name, unrelated advertisements,
and truncated packets. The reported iOS pairing name is `AA-WY-LF`; an exact name
match is only a candidate until its service identity is verified.

## Firmware and credentials

Use [atom-lite.example.yaml](atom-lite.example.yaml) as the starting point for
an **original ATOM Lite**, not an AtomS3 or another ESP32 variant. Give every
bridge a unique name and API key. The firmware enables active BLE connections,
encrypted API access, authenticated OTA updates, and USB Improv provisioning.
API/Wi-Fi client loss does not trigger automatic reboot loops. There is no web
server, open fallback access point, or cloud dependency.

Keep each device's working YAML, `secrets.yaml`, firmware binaries, and exact
network/sensor bindings outside Git in an owner-only directory. Compiled firmware
contains credentials and is also private. Wi-Fi credentials should be entered
locally, not pasted into agent chats or embedded in command arguments.

The first device's private setup directory is
`~/.local/state/atom-lite/crosstown-01/` on the Mini and Dylan's Mac. It holds
the firmware backup, build/flash records, and setup utilities. The Mini holds
the build configuration and API/OTA secrets; the laptop has the API connection
file for the attended test. No Wi-Fi password is saved by the provisioning tool.

The attended `probe_via_crosstown.py` utility on the Mini opens a temporary,
loopback-only SSH tunnel through `crosstown-mac`, runs the encrypted API probe,
then removes its temporary connection file and closes only its own tunnel.
It uses the local `probe_airvalent.py` and private `connection.json`. This is
diagnostic tooling, not a deployed OpenClaw command or a background collector.

### Attended Airvalent pairing

The default BLE capabilities could connect but pairing returned error 80
(`ESP_AUTH_SMP_PAIR_AUTH_FAIL`: authentication requirements could not be met).
An earlier `bond_mitm` configuration enabled session authentication and protected
reads, but later diagnostics found zero saved bonds. The successful configuration
uses:

```yaml
esp32_ble:
  io_capability: keyboard_only
  auth_req_mode: sc_mitm_bond
```

The generic bridge example alone cannot handle the Airvalent's PIN entry. The
installed firmware also has an encrypted `airvalent_submit_passkey` API action
bound to the verified sensor address. An owner-attended hidden native dialog
forwards a display code in memory. At the owner's request, the fixed PIN is also
provisioned separately in an owner-only runtime file for bounded recovery. Never
put it in Git, logs, command arguments, firmware examples, or model output.
The probe now waits for the actual target's SMP passkey request before opening
the dialog. Its earlier fixed-delay prompt could appear without a device code.

The tracked `components/airvalent_pair_observer/` component (copied into the
private firmware build directory) observes only the
configured sensor's passkey-request and authentication-completion events. It
logs status, negotiated mode, and bond count, never passkeys or key material.
`airvalent_bond_status` reports the stored bond count. These diagnostics confirmed
one bond before and after hardware reset and actual power removal. Protected
reads succeeded after each. Do not treat a successful pairing
response alone as evidence of durable storage.

Private `atom-lite.yaml` is the current successful build configuration.
`base-atom-lite.yaml` and `pairing-atom-lite.yaml` preserve earlier configurations;
do not redeploy those as the verified Airvalent setup. All tested images use NVS
at `0x390000` and their writes end below it. No flash erase was performed. Avoid
factory reset or erase-flash when moving power: those erase provisioning and
bond state. An ordinary unplug/replug preserved them in the verified adapter test.

## Scheduled monitoring and dashboard

`ai.openclaw.airvalent-snapshot` runs every 300 seconds on the Mini. Its locked
runtime uses `aioesphomeapi==46.12.0` and Python 3.14. `airvalent-snapshot` opens
its own temporary loopback SSH tunnel through `crosstown-mac`, validates the
expected encrypted bridge and exact sensor binding, reuses the saved bond, and
reads only the dedicated CO₂ and interval characteristics. If a stale bond is
rejected, it reconnects once. After an actual passkey request, it may submit the
owner-provisioned fixed PIN once per collection to this exact enrolled sensor.
It does not enroll new sensors, read history, or change settings. No laptop,
phone app, or local Mac Bluetooth permission is required for collection.

The collector holds a nonblocking per-sensor lock, bounds the network attempt to
110 seconds plus cleanup, and terminates only its own SSH process. Successful
reads append one `airvalent_ble_sampler_v1` room through `nest-history-append` and
its shared history lock. Ordinary failures preserve the last good cache and
retry automatically. Bluetooth authentication connection/response timeouts
(ESP-IDF codes 102/99) are `ble_connection_timeout`; incomplete pairing exchanges
and client exceptions are `authentication_incomplete`. Neither proves that the
PIN was rejected. Retry delays grow from 5 to 10, 20, then 30 minutes; the
five-minute job retries at or after `next_retry_at`, and success resets the delay.
Legacy timeout-only authentication latches recover automatically. Explicit
device rejections and missing enrollment prerequisites latch for operator repair.
After correcting credentials or attended repair, an operator can clear
`last_error` in the private status file and verify a fresh collection. The
collector records recovery timestamps, numeric authentication failure codes,
failure stage and exception class (never exception text), consecutive failures,
and the next retry time. `last_network_attempt_at` distinguishes real reads from
scheduled checks during a delay or latch. PINs and raw Bluetooth logs are never
recorded. A brief wait for the target's authentication event ensures a delayed
explicit rejection takes precedence over an earlier client disconnect exception.

`connection.json` can specify a private IPv4 `transport_host` when Crosstown's
mDNS resolution fails. The current private binding uses the verified LAN address
`192.168.165.161`; the encrypted API still verifies `crosstown-ble-01` and its key.
An ATOM DHCP reservation has not been confirmed, so this address may need updating
if its lease changes. All traffic continues through `crosstown-mac`.

`airvalent status --json` is cache-only and is the sole command exposed by the
`airvalent-monitor` skill. A read is current only when collection succeeded and
its age is no more than 15 minutes. Failed/stale reads expose `co2_ppm: null`
and separately labeled `last_known_co2_ppm`. The timestamp is `read_at`, not an
invented measurement timestamp; `measurement_at` remains null. A five-minute
sensor interval does not imply each poll produces a different value.

The climate dashboard at port 8550 displays a separate Crosstown Living Room
(Airvalent) CO₂ card and CO₂ chart series. HVAC cards remain intact across sensor
and full snapshots. Hourly history downsampling preserves the full snapshot and
each independent sensor stream. Current cache health overrides the Airvalent
card only, so a stopped collector or network outage cannot leave a permanently
fresh-looking reading. Temperature and humidity are intentionally absent until
their decoding is validated. No occupancy inference, ventilation alerts, or
HVAC control is enabled by this integration.

| Path on Mini | Purpose |
|---|---|
| `~/.openclaw/airvalent/connection.json` | Owner-only bridge API key and exact sensor binding |
| `~/.openclaw/airvalent/services.json` | Owner-only verified GATT handle map bound to that sensor |
| `~/.openclaw/airvalent/recovery.json` | Optional owner-only fixed PIN, bound to the exact sensor address and bridge |
| `~/.openclaw/airvalent/status.json` | Atomic private last-good sample and collection health |
| `~/.openclaw/airvalent/.collector.lock` | Prevent overlapping connections |
| `~/.openclaw/bluetooth/` | Tracked collector, cache projection, and locked manifest copies |
| `~/.openclaw/venvs/airvalent-monitor/` | Dedicated dependency environment |
| `~/.openclaw/skills/airvalent-monitor/SKILL.md` | Read-only model guidance |

The Mini installer copies managed Python helpers, syncs the locked environment, and
loads the sampler only when both private binding files exist. It does not create
credentials or copy enrollment secrets into Git. Seed bindings from the verified
attended setup, preserving owner-only permissions; never derive them from an
unidentified nearby advertisement. The dashboard requires `airvalent_state.py`
to be deployed alongside the collector before restarting its existing service.

For an attended rollout, verify a manual `airvalent-snapshot`, cached status,
`http://localhost:8550/api/current`, and a browser view; then verify a real
LaunchAgent run. To pause collection, boot out only
`gui/$(id -u)/ai.openclaw.airvalent-snapshot`. Preserve its cache, history, and
bindings. The next successful sample resumes the chart without rewriting old
observations. Firmware changes, initial enrollment, and repair after rejected
credentials remain attended operations. Routine PIN recovery is bounded as above.

## Validation and recovery

Before flashing, identify the chip and port, ensure no other process owns the
port, and read the **entire** flash. Preserve its size and SHA-256 record. For the
first device the serial flasher used `esptool==5.2.0`; 460800-baud reads failed,
so the successful backup and flash used 115200 baud.

After flashing, verify all of the following separately:

1. The flasher verifies the written firmware hash.
2. Improv reports a successful Wi-Fi connection.
3. An authenticated API client verifies the expected bridge name and firmware.
4. BLE advertisements include the intended sensor; do not enroll a different
   device merely because its radio signal is stronger.
5. Sensor reads match the display and are demonstrably fresh, including after
   reconnect. A successful Bluetooth connection alone is insufficient.
6. The production route works through the always-on Crosstown Mac, without
   depending on Dylan's laptop remaining awake or attached by USB.
7. A stored bond survives restart and actual power removal, followed by protected
   reads without a new pairing dialog.

For a bounded verification from the Mini (API keys remain in private files):

```sh
uv tool run --python 3.13 --exclude-newer 2026-11-09 --from esphome==2026.10.2 \
  python ~/.local/state/atom-lite/crosstown-01/probe_via_crosstown.py --bonded-read
```

Use `--pair` only for attended enrollment with a fresh display code. `--status`
checks the encrypted API and bond count without connecting to the sensor.
Routine reads go through `crosstown-mac`; laptop USB is needed for the initial
flash and native enrollment dialog, not for normal Bluetooth transport.

The first device's `factory-backup.bin` is exactly 4,194,304 bytes. To restore it,
connect that same ATOM by USB, re-identify its port/chip, verify the saved checksum,
and use esptool's `write-flash 0 factory-backup.bin` at 115200 baud. Restoration
replaces the bridge firmware and provisioning. Do not restore another board's
backup or erase the backup during troubleshooting.

## Airvalent protocol investigation

The initial target is the Crosstown Airvalent. A third-party reader provides
evidence of an accessible service and CO₂ history format:
[IndoorCO2Map AirvalentProvider](https://github.com/AurelWu/IndoorCO2MapApp/blob/c4179ad0697fcc17bded0a9c2f1df4ae748eec31/CO2Monitors/AirvalentProvider.cs).
This is a protocol reference, not proof that this particular monitor is enrolled
or readable through ESPHome.

Use `subscribe_bluetooth_le_raw_advertisements` with the tested ESPHome
2026.10.2/aioesphomeapi 46.12.0 combination. The older parsed-advertisement
subscription yielded no messages even while the bridge was receiving BLE
traffic. Keep the advertisement subscription open for GATT operations, since
the proxy routes connection replies to its subscriber. Only one collector
should own that subscription at a time.

Cache the discovered service map only when it is nonempty and bound to the
verified sensor address. ESPHome's cached connection mode does not rediscover
services; use the retained map for reads. The diagnostic's first discovery can
authenticate as soon as the link opens, before the MTU completion event. Its
pinned API adapter ignores a positive connection-establishment message while
waiting for authentication, but fails on an actual disconnect. This is diagnostic
code requiring further reliability testing, not a production collector.

The reference app's
[monitor identifiers](https://github.com/AurelWu/IndoorCO2MapApp/blob/main/CO2Monitors/MonitorTypes.cs)
distinguish the Airvalent's advertised UUID byte order from the connected GATT
service UUID. Check both wire representations before concluding the device is
absent. Do not connect to an unidentified nearby device by RSSI alone.

The reference warns that history reads can leave a cursor pointing at older
records. Do not label a plausible decoded value as a fresh current measurement
without resolving cursor/timestamp semantics and comparing against the device.
The probe reads interval, chunk count, history, and the dedicated read/notify
characteristic `e8694d30-a155-4fb4-befa-548e64c88965`. That characteristic returned
eight bytes; the low 14 bits of the first little-endian 16-bit value decoded to
649 ppm, matching the display and the last history record byte-for-byte. This is
an observed protocol result, not a manufacturer specification. Record-timestamp
semantics, temperature, and humidity decoding have not been validated.

Pairing was explicitly authorized and used the attended flow above. The probe
does not change sampling intervals, calibration, display settings, or the history
cursor. It disconnects and closes its own tunnel after each bounded attempt.

The first history read ended at 719 ppm while the owner subsequently reported
750 ppm and then 694 ppm on the display. Those asynchronous historical readings
were not validated as current. The later dedicated-characteristic/display match
is the verified reading above. The interval characteristic reports 300 seconds;
do not assume every API poll produces a new sensor measurement.

## References

- [M5Stack ATOM Lite hardware](https://docs.m5stack.com/en/core/Atom-Lite)
- [ESPHome BLE proxy behavior and compatibility](https://esphome.io/components/bluetooth_proxy/)
- [Official ATOM Lite configuration](https://github.com/esphome/bluetooth-proxies/blob/main/m5stack/m5stack-atom-lite.yaml)
- [Improv serial provisioning protocol](https://www.improv-wifi.com/serial/)

## Rollout verification (November 8, 2026)

The saved bond and protected reads passed a hardware reset and an actual power
move to a standalone adapter. During scheduled-monitoring rollout, a later
connection requested fresh pairing despite the ATOM retaining its bond. The
cause is not established; possible replacement of the peripheral's bond by a
phone connection remains unconfirmed.

The owner confirmed that the display PIN is fixed and requested private storage
for reuse. An initially mistyped saved PIN was rejected; the failure latch
prevented repeated attempts. A corrected hidden-dialog entry was saved with
mode `0600` in `~/.openclaw/airvalent/recovery.json`. No 1Password backup was
created: its CLI lookup timed out.

The scheduled job is installed at 300 seconds and the skill is eligible in
OpenClaw. Actual LaunchAgent runs were triggered for verification. A normal read
succeeded at 18:52:26 UTC; on the next connection, automatic fixed-PIN recovery
occurred at 18:53:03 UTC and a protected read succeeded at 18:53:12 UTC with exit
zero. A separate subsequent connection read successfully at 18:53:43 UTC without
another PIN submission. Successful samples reached the shared history and the live dashboard's
Airvalent CO₂ card and series; failed attempts and old diagnostic readings were
not inserted as fresh history. These tests establish working bounded recovery,
not permanent bond retention or long-term reliability.

## Connection-timeout recovery (November 9, 2026)

The last good reading before the outage was November 8 at 21:32:59 UTC
(16:32:59 EST). The retained failure was `bond_unavailable` with authentication
code 102, and subsequent five-minute jobs skipped the network because the error
was latched. The dashboard correctly marked the retained reading stale.

The installed ESP-IDF 5.5.6 headers identify 102 (`0x66`) as
`ESP_AUTH_SMP_CONN_TOUT`: `HCI_ERR_MAX_ERR` (`0x43`) + 10 + `SMP_CONN_TOUT`
(`0x19`). The collector now preserves the pairing-response failure code, falls
back to the target-filtered firmware event, and classifies this specific timeout
as retryable. It also recognizes legacy timeout latches without discarding the
last good sample. Actual PIN failures and unknown authentication failures remain
latched; the one-PIN-per-run limit and exact sensor binding are unchanged.
Failure timestamps are stored separately from scheduled attempt timestamps.
The original radio-disconnection trigger is unknown.

Deployed and verified with an actual LaunchAgent run: automatic saved-PIN
recovery at 19:27:55 UTC, a fresh 691 ppm read at 19:28:03 UTC, and the same
new sample in the climate history and current API. No old readings were
backfilled as current observations; the collection gap remains visible.

## Incomplete-authentication recovery (November 10, 2026)

Collection stopped after the November 10 02:00:01 UTC reading (November 9,
21:00:01 EST). The 02:05:42 UTC failure was `reauth_required` without a numeric
authentication failure, shortly after a PIN submission. The old broad exception
handler and post-pairing bond check both produced that permanent latch; the
retained data cannot distinguish which path caused this particular failure.
The original exception was not preserved.

An instrumented live check found zero stored bonds and a connection timeout
(code 102, disconnect reason 8). The corrected collector separates client/API
exceptions and missing post-pairing bond confirmation from explicit device
rejections. Ambiguous failures now retry with bounded backoff. It preserves safe
failure-stage/type details and allows delayed target rejection events to arrive
before classification. No firmware, PIN, sensor binding, or network routing was
changed. This incident’s unclassified latch was released after a private state
backup; other unclassified legacy latches are not silently cleared.

The first deployed run recovered using the existing saved PIN and read 567 ppm
at 12:38:23 UTC. The outage gap remains in history; no old readings are presented
as fresh data.

The next ordinary LaunchAgent run (no manual trigger) started at 12:43:24 UTC
and successfully read 567 ppm at 12:43:33 UTC. Both readings reached the
climate history/current API, with all 15 current rooms preserved. The browser
showed the recovered Airvalent card and CO₂ series. All 53 targeted collector,
dashboard, and history-appender tests passed. Longer-term radio reliability is
not established by these two reads; the fix prevents ambiguous failures from
permanently disabling collection.
