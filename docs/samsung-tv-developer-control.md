# Crosstown Samsung TV developer control

Verified 2026-10-24 on QN65LS03BAFXZA (2022 Frame), firmware
T-PTMAKUC-1720.7, Tizen 6.5 / ARMv7. Developer Mode is enabled for the
Crosstown MacBook's LAN address, 192.168.165.109. The TV is 192.168.165.2.
Use SSH host `dylans-macbook-pro`; `dylans-mac` is a different machine.

## Current maintenance state

As of the completed 2026-10-24 full restart, all 24 targeted app-disable records
and six vendor stop markers survived. Targeted voice, advertising and TV Plus
processes were absent in initial and delayed checks. Shared audio services
started normally. Four extra AOV/far-field runtime masks were reapplied after
verification; those masks expire at every full reboot.

See the [privacy catalogue's current status and remaining checks](samsung-tv-privacy-catalogue.md#current-status-and-remaining-checks)
for the authoritative summary, recovery versions and resource baseline. Earlier
dated sections below describe their phase's observations, not current pending
work. Full-restart persistence is now observed; separate standby/wake and
post-restart playback/integration checks remain outstanding. The October 25
network-wake investigation below adds an attended off/wake check, without
closing the separate privacy-guard and playback validations.

## Crosstown transport and pairing recovery (2026-10-25)

Julia's control attempt exposed a separate transport failure: Mini-to-TV subnet
traffic depended on the other Crosstown workstation, which was offline, while
SSH developer commands through the always-on MacBook succeeded. All TV-LAN
connections now use `dylans-macbook-pro`. The Mini's `samsung-tv` CLI creates
short-lived loopback SSH forwards for REST and WebSocket ports 8001/8002;
`samsung-tv-dev` already performs its LAN operations on that MacBook. There is
no direct subnet fallback. SSH failures report a transport problem rather than
asserting the TV is off or awaiting approval.

HTTPS and WSS are preserved through the forwards. Credentials stay on the Mini
in a private token file. Each command owns and cleans up its SSH process, and a
closed parent stdin pipe also releases SSH after abrupt parent termination.
This adds no persistent service, dependencies, or tailnet routing changes.

Moving the connection source required attended pairing during this migration.
The TV also emitted `ms.remote.touchEnable` / `ms.remote.touchDisable` before
its connection acknowledgement; the library rejected those notices. The helper
now tolerates those specific notices within a bounded handshake, still requiring
`ms.channel.connect` before saving a token. Unauthorized or unexpected responses
close the connection without retrying. `samsung-tv pair frame` makes one
60-second approval attempt and sends no keys. One accepted request saved the
token, and a fresh connection authenticated successfully with it.

All 96 Samsung TV tests passed. Live checks verified the expected TV identity,
HTTP/HTTPS REST, and authenticated remote WebSocket access while direct Python
socket connections from the Mini to the TV were deliberately blocked. Art
WebSocket URL routing was checked without changing Art Mode. Normal cleanup and
SSH exit after abrupt parent termination were also verified. No remote keys,
power changes, or playback commands were sent during these transport checks;
the attended off/wake cycle below predates this routing change.

### Public Netflix lookup recovery

The same request also reproduced `IncompleteRead` on both urllib attempts to
read Netflix's public page for *Below* (`81652677`). The system curl client
retrieved the complete page and the existing parser matched the exact title.
`content_lookup.py` now falls back to `/usr/bin/curl` only after both urllib
attempts are incomplete. The fallback disables user curl configuration, checks
each redirect against the provider allowlist before requesting it, caps the
body at 8 MiB and the fallback at 25 seconds, and rejects failed or partial
downloads. It uses no account session or new dependency.

The deployed helper resolved all six episodes and independently resolved
Limited Series episode 1, *Come Home Year*, to `81943170`. This verifies public
metadata, not profile viewing history or playback. All 99 Samsung TV tests
passed, including fallback failure, redirect-boundary, and size-limit checks.

### OpenClaw replay

An isolated main-agent gateway session replayed Julia's *Below* request with her
later clarification to use Dylan's profile. It loaded the deployed skill,
verified power, launched Netflix, used the repaired remote connection, visually
selected Dylan's yellow profile, and submitted the native series-play request.
The replay had begun before the lookup fix was deployed and resolved title
identity from Netflix's public page instead. No iMessage reply was delivered.

Post-launch captures showed a rating overlay and later subtitles over protected
black video. The agent correctly reported playback as not fully verified: no
identifying episode controls or visible video were captured. Capture staging
cleanup was confirmed. The CLI request succeeded and the prior remote-key
failure did not recur, but this evidence alone does not confirm the episode or
visible playback. Sending UP before root capture setup finishes can miss the
transient player controls, as noted in the earlier diagnostic below.

## Unresolved standby wake failure (2026-10-25, later evening)

The subsequent Chillhop / Spotify request used the updated Crosstown transport
and completed the 90-second readiness wait, but returned `power: unknown`,
`wake_packet_sent: true`, and no power key. The owner confirmed the TV remained
off. SSH to the MacBook worked; direct MacBook-to-TV probes could not reach
REST, WebSocket, or SDB, and the TV's ARP entry was incomplete. This is distinct
from the earlier workstation subnet-route failure.

A narrowly filtered packet capture on the MacBook's `en0` verified the expected
102-byte UDP wake broadcast from `192.168.165.109` to `192.168.165.255:9`, with
Ethernet broadcast destination. The TV remained unreachable afterward. A second
diagnostic sent that magic packet directly to the known TV Wi-Fi MAC and IP;
packet capture confirmed the unicast destination. Its temporary TV-only ARP
mapping was removed immediately after sending, and REST remained unavailable
for another 95 seconds. Capturing transmission from the MacBook does not prove
receipt or processing by the sleeping TV.

Neither test justifies increasing the readiness timeout or installing the
unicast experiment as a fix. At this point, standby wake settings and the
SmartThings wake path still needed inspection once the TV became reachable. The
previous immediate off/wake success does not establish reliable longer standby
wake. No TV settings, router settings, or persistent neighbor entries were
changed in these diagnostics.

At the owner's request, a subsequent diagnostic reused the existing Google
Assistant text transport from the installed Roomba helper's `send_command`
function, without invoking a vacuum command or changing that helper. The exact
request was `Turn on Samsung The Frame 65`. Google acknowledged that request,
but local REST remained unavailable through the following 86-second check and
the follow-up `Is Samsung The Frame 65 on?` returned that it was off. This is
not a verified wake fallback; do not equate Google's acknowledgment with actual
TV power. The existing OAuth workflow was used without printing credentials,
relinking an account, or changing permissions.

A final bounded test sent four wake-only packets, covering both
`255.255.255.255` and `192.168.165.255` on UDP ports 7 and 9. Packet capture
confirmed all four transmissions; the TV remained unavailable throughout the
95-second observation window. A fresh Crosstown DNS lookup still resolved
`Samsung.lan` to `192.168.165.2`, and the known TV MAC was absent from the
MacBook's neighbor cache. No address change was demonstrated. A DHCP reservation
could stabilize future addressing, but these results do not establish it as a
fix for the standby wake failure.

After IR power-on, the same model, Wi-Fi MAC and IP returned. At the first SDK
inspection, uptime was approximately 52 seconds: this was a fresh boot. SDB
became available before REST; MSF was still inactive at that first check and
subsequently started normally. The Wi-Fi driver reported magic-packet wake
enabled, and its device wake attribute was enabled. Scoped reads of the actual
Buxton configuration store established:

- `db/menu/network/wakeOnMobile_enable`: Int32 `1`.
- `db/menu/network/ip_remote_support`: Int32 `1`; TCP 1516 was listening.
- `db/menu/network/wifi_power`: Boolean `true`.
- `db/menu/system/power_control/network_standby`: Int32 `0`; applicability to
  this consumer Frame is not established and it was not changed.
- `db/boot/instant_on`: Int32 `1`; `db/sysman/always_instanton`: Int32 `0`.
- The recorded power-off reason was `POWER_KEY`.

Recursive `vconftool` enumeration returned no entries on this firmware, despite
successful exact-key reads. Key-name discovery used read-only SQLite access to
`/opt/var/lib/buxton2/system.db`, restricted to power/wake-related names, followed
by exact-key reads. No account or pairing records were retrieved. Temporary
root sessions verified unchanged system-file hashes and staging cleanup. One
SDK diagnostic timed out; both exact reported staging paths were independently
verified absent and the owned SDB server was stopped.

The normal Expert Settings UI also showed Power On with Mobile and IP Remote
enabled. An attempted off/on refresh disabled Power On with Mobile, but the
menu closed during screenshot capture and subsequent keys did not restore it.
The original Int32 value `1` was restored through `vconftool set -f -t int` for
that exact key. A separate SDK read confirmed both wake-on-mobile and IP remote
were `1` before any further power test. This was a restoration of the recorded
baseline, not a verified wake fix or a completed UI refresh. Temporary root
staging and the owned SDB listener were confirmed cleaned up.

The subsequent bounded trial began at 01:19:18 UTC on October 26 (October 25
locally). One off command was followed by a passive REST result of `standby`,
then 180 seconds with no TV probes. One standard `samsung-tv power frame on`
completed in 27.4 seconds with `power_on_verified: true`,
`wake_packet_sent: true`, `power_key_sent: true`, and `power: on`. The owner
confirmed the screen came on without using the remote. Post-test SDK reads
confirmed both settings remained `1`; the boot ID differed from the original
IR-started boot. Diagnostic staging and the owned SDB listener were cleaned up.
This establishes one successful three-minute standby/wake cycle. It does not
establish the cause of the earlier failure, prove the setting refresh caused
recovery, or validate longer standby reliability. The TV was left on.

During the follow-up, the owner reported renaming the device to **TV**. Use that
name for subsequent Google Home requests; earlier exact-name commands above
record the previous name. Local control still identifies the configured Frame
by its fixed IP/MAC and the `frame` CLI alias.

The first rename update missed Spotify's exact-name check. The resulting
playback failure was separate from TV power: Spotify exposed an unrestricted
device named `TV`, while the helper still required `Samsung The Frame 65`.
Both Spotify selection and status checks now use `TV`. The local remote's
default and installed configuration also use `TV`, with `frame`, `crosstown`,
and the old full name retained as aliases. Its Crosstown pairing identity
remains the original token-file namespace, independent of the display name.
The developer/content helper targets the IP/MAC and required no rename change.
TV and Spotify-speaker skill copies were updated together.

All 103 Samsung tests passed, including renamed Spotify selection/status,
old/new local configuration aliases, and pairing-token continuity. Live REST
verified `TV` on, and a no-key WebSocket handshake succeeded using the existing
pairing identity. A single request for Chillhop Radio playlist
`6mn9kuAlefrqO6omLQxJ2N` was accepted for `TV`; Spotify reported it active and
playing **What If I Told You** (`4BwLji03aUrqk6fmbiwqGT`). The status interface
does not expose playlist context. The owner subsequently confirmed audible
playback from the TV. No playback write was repeated for verification.

## Cold-wake readiness recovery (2026-10-25)

The Cyberpunk / Dylan-profile request loaded the current skill and sent LAN
WoL through the correct Crosstown Mac. Its power helper returned
`wake_packet_sent: true`, `power_on_verified: false`, `power: unknown` and
stopped before app launch or profile selection. No remote connection was
attempted, so the generic suggestion of an approval prompt was unsupported.

The router still resolved `Samsung.lan` to the configured `192.168.165.2`.
A subnet probe, known-MAC neighbor check and discovery checks found no live TV
before IR wake. IR brought back the same IP and Wi-Fi MAC. SDB became reachable
before ports 8001/8002; the latter initially refused connections, then recovered.
The TV had freshly booted, and systemd recorded `msf-server.service` starting
about 61 seconds after boot. The Wi-Fi driver reported magic-packet wake enabled.

An attended reproduction turned the screen black, briefly returned REST
`standby`, then lost REST reachability. The original helper sent WoL and
reported failure. Continued passive observation subsequently found SDB and
then REST `standby`; a single verified remote-power fallback produced `on`.
This establishes a readiness-timeout defect in that reproduction. It does not
prove every earlier wake packet arrived or that all overnight wake failures
have the same cause. No wake preference, privacy guard, or router setting was
changed during this investigation.

The Mini's tracked `bin/samsung-tv` now waits for REST `on` **or** `standby`
within a 90-second readiness window after one LAN wake packet (an in-flight
request can finish after that deadline). Confirmed standby proceeds immediately
to the existing remote fallback. The short post-key verification remains
separate. Unknown state still cannot authorize a toggle, and no key or wake
packet is repeatedly sent. The status command also catches failures during
client construction; this library can query model metadata in its constructor.
Such failures now report `UNREACHABLE` instead of an uncaught traceback.

All 83 Samsung TV tests pass, including delayed standby after 64 seconds,
delayed direct-on, immediate standby, the readiness deadline, and constructor
failure. Syntax and whitespace checks pass. The installed Mini CLI is a symlink
to this tracked source, so no gateway restart or separate CLI deployment is
needed. One diagnostic callback failed during startup; both exact staging paths
were subsequently verified absent, and later SDK/host cleanup was confirmed.

Live validation with the updated helper: after one power-off command, REST
reported standby at 5 and 15 seconds and was unreachable at 32 seconds. The
subsequent single `power frame on` invocation completed in approximately
47 seconds with `wake_packet_sent: true`, `power_key_sent: true`,
`power_on_verified: true`, and `power: on`. No IR intervention was needed for
that cycle. No app launch, profile selection, or content playback was attempted.
This is an immediate off/wake validation, not an overnight-standby endurance test.

## OpenClaw standby/capture recovery (2026-10-24)

The Love Is Blind / Julia-profile request exposed a sequencing problem:
OpenClaw launched Netflix before checking panel power, treated the acknowledgment
as visible launch, and stopped after an ineffective LAN wake. Its later
`KEY_POWER` request did produce REST `on`. No profile selection or playback was
established by that exchange.

The original capture error was **unconfirmed SDK staging cleanup**, not a missing
screenshot utility or loss of root access. The SDK wrapper allowed an operation's
`exit` to bypass its result/cleanup footer; foreground detection has such early
exits. This is consistent with capture attempted in standby, though the original
error did not retain enough foreground output to prove which branch ran. Both
exact staging paths named in the failure were subsequently verified absent.
The wrapper now isolates operation exits in a subshell, reports command failure
separately from cleanup, and still rejects unconfirmed cleanup.

Use `samsung-tv power frame on` before an authorized app/play request:

- Already-on returns verified status without opening a WebSocket or sending keys.
- Confirmed standby opens the remote, rechecks power (connection can wake the
  panel), and sends one `KEY_POWER` only if still in standby, then polls status.
- Unknown/unreachable first tries LAN WoL through the Crosstown Mac. An unknown
  state never authorizes a toggle. Failure exits nonzero; do not blindly retry.

The deployed skill requires power verification, observed profile selection, and
one bounded screenshot recovery after a state change when cleanup is confirmed.
Unconfirmed cleanup still requires maintainer inspection of the reported paths.
Screenshot evidence is private under
`~/.local/share/samsung-tv-tools/changes/20261024-openclaw-diagnostic/`.
This investigation does not establish Julia's selected profile or requested
content playback. It also does not complete the separate standby/privacy-guard
validation described in the privacy catalogue.

Validation: all 80 Samsung TV unit tests passed, including early shell exits,
failed cleanup, standby fallback, connection-induced wake, unknown power state,
and no-repeat behavior. Python syntax and Git whitespace checks passed. The
updated helper captured Netflix's promotional screen with root/staging cleanup
verified; the new power-on command reported already-on without sending a packet
or key. The corrected skill copy is deployed on the Mini and both CLI paths
resolve to tracked sources. A fresh off/on cycle with the new orchestration was
not performed, to avoid interrupting viewing; its fallback is unit-tested and
the original exchange supplies evidence for the underlying remote power command.

Subsequent successful OpenClaw attempt: the corrected power check returned
already-on, screenshot capture worked, and Return dismissed Netflix's screensaver
to reveal the chooser. Captures showed Julia's highlighted blue profile, then
Netflix home with that avatar after Enter. A single native *Love Is Blind*
series request reached an episode with resume progress and readable subtitles;
the owner subsequently confirmed success. The [Netflix skill reference](../openclaw/skills/samsung-tv/references/content.md#profile-selection-and-series-resume)
records the reusable sequence. This closes the original profile/content request,
but the attempt began already-on and does not add a fresh standby wake test.

## Full remote restart (maintainer operation)

Verified on this exact model/firmware through the Crosstown MacBook on
2026-10-24. The exact TV-side command dispatched by the gated root-owned job was:

```sh
/usr/bin/busctl --system --timeout=5 call \
  org.tizen.system.deviced \
  /Org/Tizen/System/DeviceD/Power \
  org.tizen.system.deviced.power Reboot s Unknown
```

Live introspection confirmed `Reboot(s) -> i`; the changed kernel boot ID and
reset uptime established the actual restart. `Unknown` is the generic reason
used in this verified call. This is not a factory-reset operation. The firmware
lacks `reboot.target` and `systemd-reboot.service`, so `systemctl reboot` failed;
`/usr/sbin/reboot` is a symlink to systemctl and is not an independent fallback.
There is no `samsung-tv-dev reboot` subcommand. Ordinary remote off/on or WoL
does not establish a fresh kernel boot.

Use this only for an owner-authorized full restart. An explicit reboot request
already supplies interruption authorization. The command must be dispatched
after the temporary root session has cleaned up, using the verified two-phase
procedure; do not run it directly inside an active archive-root probe.

1. Capture the current boot ID, guard state and recovery location. Verify the
   target/model and check that `fw-update-service.service`,
   `fw-usb-update-service.service`, `swu-core-tv.service`,
   `bootmode-swu-standalone.service` and `bootmode-system-update.service` are
   inactive. An active update scheduler alone is not an active installation.
2. In the reviewed root session, stage a uniquely named, collected transient
   timer with `--on-active=90s`, `AccuracySec=1s`, and a bounded dispatch service.
   Its inline command checks a unique readiness marker, removes its own gate
   files, repeats the update checks, then executes the command above. Without
   the marker it exits without rebooting. Do not install a persistent boot hook.
3. Wait for verified root staging removal, unchanged protected system files and
   owned host SDB cleanup. Only then use a separate SDK session to create the
   readiness marker, checking the original boot ID and at least 20 seconds
   remaining before dispatch. That SDK session must also finish cleanup before
   dispatch. This is an internal cleanup gate, not another user-approval prompt.
   If cleanup or timing is uncertain, leave the gate unarmed and inspect/clean
   only the owned transient job.
4. Observe reconnection, then require a changed `/proc/sys/kernel/random/boot_id`
   and reset `/proc/uptime`. Reachable SDB, an API acknowledgment or a visible
   startup screen alone is insufficient. Check persistent flags/markers and
   process absence **before** restoring extra runtime masks, then verify shared
   audio and the foreground app. Record the owner's screen observation separately.
5. Allow startup work to settle and label the measured workload. The verified
   run's early two-minute sample was still busy; its later baseline started at
   259 seconds, on idle HDMI, and is not directly comparable to Netflix playback.

Reviewed scripts and evidence are in the Mini's protected local directory
`~/.local/share/samsung-tv-tools/changes/20261024-reboot-baseline/`:
`schedule-vendor.sh`, `approve-vendor.sh`, `schedule-vendor.txt`,
`approve-vendor.txt`, `postboot.txt`, `postboot-delayed.txt`, and
`startup-verification.json`. The root runner is
`~/.local/share/samsung-tv-tools/changes/20261024-privacy-performance/root-run.py`.
These are evidence from one run: their boot ID, timer name, gate and deadlines
must be regenerated for a later invocation, not replayed verbatim. Review the
runner and pinned assets before reuse. Preserve the original records.

## Working controls

From the dotfiles checkout on the Mini:

```sh
bin/samsung-tv-dev status
bin/samsung-tv-dev foreground
bin/samsung-tv-dev screenshot --output /tmp/frame-UNIQUE.png
bin/samsung-tv-dev apps Internet
bin/samsung-tv-dev browser
bin/samsung-tv-dev launch APP_ID
bin/samsung-tv-dev app-info APP_ID
bin/samsung-tv-dev app-state APP_ID
bin/samsung-tv-dev open 'https://example.com/'
bin/samsung-tv-dev launch APP_ID --url 'CONTENT_URI'
bin/samsung-tv-dev youtube 'https://www.youtube.com/watch?v=VIDEO_ID'
bin/samsung-tv-dev youtube-state VIDEO_ID
bin/samsung-tv-dev netflix 'https://www.netflix.com/watch/80094030' # Restarts Netflix
bin/samsung-tv-dev apple-tv 'https://tv.apple.com/episode/umc.cmc.23qox5n6larygqgg6u74fvyj'
bin/samsung-tv-dev apple-tv-play 'https://tv.apple.com/episode/umc.cmc.23qox5n6larygqgg6u74fvyj'
bin/samsung-tv-dev prime-video amzn1.dv.gti.22e31bb7-24ed-4ee1-a15c-800a1e9da25f
bin/samsung-tv-spotify status
bin/samsung-tv-dev wake
```

The helper sends its source over SSH to the MacBook, checks the pinned SDB
binary, connects to TV port 26101, and performs one bounded operation. It
refuses a pre-existing SDB server and serializes its own operations. SDB binds
host TCP 26099 on all interfaces, so the helper terminates its verified owned
server afterward, including on command failure. No persistent daemon is installed.
An ownership/cleanup error requires inspection; never broadly kill SDB processes.
Native app-state, manifest, and URI operations use the authorized SDK-user
command-injection path with temporary scripts, nonce-checked callbacks, UID 901
checks, bounded responses and verified normal cleanup. Ordinary app launch and
discovery use the developer service directly. YouTube playback uses DIAL plus
the YouTube playback-control API instead of SDB; wake uses a LAN broadcast.

The browser's working launch ID is `com.samsung.tv.knox-browser`.
SDB acknowledged its launch and the owner confirmed it appeared on screen.
The helper reports launch acknowledgment separately from visual observation.
WebSocket URL metadata did **not** navigate the existing browser page.
Ordinary SDB shell/root access is disabled; debugging the built-in browser
returned `closed`. The separately authorized SDK-user diagnostic below established
URL navigation through native app control. Browser DOM inspection remains unresolved.

### Title-to-ID lookup

The skill now includes `scripts/content_lookup.py` and
[`references/lookup.md`](../openclaw/skills/samsung-tv/references/lookup.md).
OpenClaw searches for provider links, then uses this read-only helper to check
YouTube title/channel metadata or extract Netflix's exact episode IDs from its
public title page. No account session, new API key, TV connection or root is
needed for lookup. Discovery remains a model/search step, not a first-result
autoplay rule. Ambiguous or absent episode matches fail without a series fallback.

Live public checks on 2026-10-24 covered *Our Planet* / Jungles, *The Queen's
Gambit* / Exchanges, *The Sea Beast*, and Netflix's YouTube upload of
*From Deserts to Grasslands*. No playback was changed during lookup validation.
The public Netflix data format is not a stable API: parsing changes fail closed,
and observed truncated responses receive just one retry. Public availability
does not verify TV account/profile entitlement. See the reference for commands.

The deployed script and reference files were compared byte-for-byte with tracked
source, and the deployed script resolved both Jungles and Exchanges. A fresh
`openclaw skills info samsung-tv --json` check was unavailable in this shell
because the gateway-token environment reference was not loaded. No gateway
restart or secret/configuration change was made; a full natural-language request
through OpenClaw remains an integration check, separate from these lookup tests.

## Additional-app survey (2026-10-24)

The [installed-app survey](../openclaw/skills/samsung-tv/references/apps.md)
records real app IDs, package versions, provider lookup examples and candidate
content routes for Prime Video, Apple TV, HBO Max, Peacock, Spotify, Crunchyroll
and Twitch. All seven were confirmed installed. The initial read-only survey
was followed by owner-authorized live validation, recorded below.

Read-only package inspection established native Aria targets for Prime/Apple,
Smart Hub handlers for HBO Max/Peacock/Crunchyroll/Spotify, and hosted launch
entrypoints. Public HBO Max/Twitch JavaScript contains explicit Samsung PAYLOAD
parsers, documented with source URLs and hashes in the survey.

One SDK diagnostic exceeded its output bound while attempting to summarize
static manifests. Its cleanup was dispatched; a subsequent bounded SDK check
found no retained prior diagnostic script and confirmed its own cleanup.
Three temporary root resource inspections authenticated UID 0 and confirmed
protected system files unchanged, staging removed, and owned SDB stopped. The
last inspection timed out during a static Apple `app.js` pattern scan; later
commands in that diagnostic did not run. No credential stores, viewing history,
profile databases or app code were changed. Raw vendor resources remain outside
Git; only the actionable summary is deployed with the skill.

## Additional-app live validation (2026-10-24)

All seven native apps opened and were identified by focused-app capture.
Prime Video and Apple TV reached their profile choosers with Dylan highlighted;
Peacock reached its profile chooser. Crunchyroll reported an expired session
(`WCP-UN-0`), so further playback tests require owner login. No credentials,
new profiles, subscription offers or purchases were submitted.

Twitch accepted a full channel URL in `PAYLOAD` with the default operation,
using the normal SDK account. BobRoss's live marathon appeared with a Pause
control and the owner confirmed video playback. Monstercat's channel page was
also selected and explicitly reported offline; that is channel-selection proof,
not playback proof. VODs remain untested. The typed `twitch` helper rejects
non-provider URLs, credentials, query/fragment additions and non-channel paths;
it reports submission separately from playback. A foreground channel-change
request left the same Monstercat surface and PID, so the helper gracefully
restarts Twitch with a bounded stop check before sending its one payload.
Unknown state or incomplete termination aborts rather than force-killing or
blindly retrying. The revised helper selected BobRoss in a new process; the
captured pre-roll explicitly named the requested channel.

HBO Max direct routing is now working. Its temporary per-app inspector showed
that `launch_app` splits comma-delimited payloads into an array while HBO Max
reads only the first value. Native warm launches also failed to dispatch the
appcontrol event. The final helper gracefully stops the app and uses
`aul_test launch APP PAYLOAD STRING`, preserving one complete payload. This CLI
returns the PID as its shell status; the helper normalizes transport status and
requires exactly one positive-PID success record without retrying writes.
The Last of Us show page was visually verified; its direct video request played
The Last of Us, confirmed by the owner. The exact episode for that catalog link
was not independently established. `max-show`, `max-play` and `max-search` use
the normal SDK account and need no app patch, root or inspector.

Peacock's running hosted code revealed an encoded `deeplinkData` JSON object
inside a URL, itself carried in `PAYLOAD={"values":"URL"}`. A public watch URL
alone was insufficient. The public Pilot UUID works as `pvid`; the separate
`GMO_...` content ID did not. Search results and **Pilot, S1 E1** highlighted in
the episode list were captured. The final native helper restarted Peacock and
submitted the exact Pilot PLAY request. The owner subsequently confirmed
**The Office S1E1, Pilot** playing. A read-only check confirmed Peacock remained
foreground in the same process; no additional playback request was sent.
See the skill's [app reference](../openclaw/skills/samsung-tv/references/apps.md)
for commands, exact formats and hashed public source evidence.

For future bounded diagnosis, the installed `app_launcher -w --start=APP`
starts a single web app with a temporary CDP inspector and reports its port.
Gracefully stop the ordinary instance first, and inspect only the intended app.
This is also described in Samsung's
[web-app debugging CLI](https://github.com/Samsung/tizen-agent-skills/blob/main/tizen-sdk-skills/common/lib/cli/webapp-debug-cli.js).
The inspector allowed routing/control inspection and retrieval of already-loaded
public code without reading cookies, account stores or DRM keys. The Peacock
canvas UI still required the existing root surface capture; CDP screenshots
timed out. Close diagnostics by gracefully stopping that app and relaunching it
normally, then verify the old port no longer accepts connections. The HBO Max
and Peacock diagnostic ports were both verified closed after native launches.
No global debug setting, security policy or persistent root service changed.

An earlier local copy of the native launcher with a changed delimiter could not
execute under the TV's policy. Both temporary copies were removed; the original
binary was untouched, and no policy relaxation was attempted. The successful
route uses the unmodified installed launcher. Earlier `wrt-client --help` and
bounded-resource inspection failures were cleaned up; the later inspector made
those approaches unnecessary.

Apple TV now has separate native `apple-tv URL` (details) and `apple-tv-play URL`
commands. Read-only inspection of shared `libariaappmgr.so` (SHA256
`21feb82b88c18913296e87af8799b5817c2215a17373d300abe04d239bf04811`)
established the `PAYLOAD` envelope: JSON `values` must be a string. Apple's
installed public `app.js` routes that provider URL and honors `action=play`.
The detail request survived Dylan's profile selection; the captured UI identified
Severance S1E2 **Half Loop**. A warm play request started a trailer, followed by
owner-confirmed Half Loop playback. The typed helper's canonical URL also
selected that exact episode page. It strips supplied query parameters, checks
installation, sends once and reports acknowledgment separately from playback.
No app restart, root launch, app patch or account-file inspection was needed.

Prime Video's native Aria GTI request also worked through Dylan's profile
chooser: Fallout **S2E1 The Innovator** appeared with **Play S2 E1**. Selecting
that visible Play control started ads, then the owner confirmed episode playback.
The GTI was mapped from Prime's own public season-page metadata. The typed
`prime-video GTI` command submits that exact identity, with no blind keys,
automatic restart or playback claim. A later request through the final helper
showed Dylan's profile chooser again with the same Prime process. The skill
therefore requires observing profile selection rather than assuming it persists.
After selecting the observed Dylan profile, the final helper's capture showed
Fallout **Resume S2 E1**, the matching episode synopsis and **Included with Prime**.
The TV was left on that detail page. Direct autoplay remains unverified.

Spotify is now verified through `bin/samsung-tv-spotify`: a narrow wrapper
around an isolated, source-built `spogo` 0.10.3 targeting fix. Queen's Bohemian
Rhapsody was identified on the native TV screen, reported playing on the Frame,
and audibly confirmed by the owner. Resume and pause through the final installed
helper reported the correct device and state. The helper uses existing local
Spotify authentication and never changes the saved profile or default device.

Homebrew spogo remains unchanged. Its device-targeting bug and the failed native
PAYLOAD attempts are documented in the app reference. The isolated client is
reproducible using `openclaw/skills/samsung-tv/scripts/build_spogo_targeted.py`
and its tracked patch. The build pins upstream commit and patch identity, runs
upstream Spotify/app tests plus explicit-target regressions, and compiles with
local Go 1.27.3. A verified build receipt and digest guard the installed binary.
The wrapper forces Connect, resolves the unique Frame TV to an exact device ID,
and never retries a playback write when status is delayed. Missing, ambiguous
or restricted devices fail without falling back to another player. Album and
playlist inputs are accepted but have not received separate TV playback tests.

Focused captures used temporary root, with staging cleanup and unchanged
protected system files verified. Launch requests required no root. Captures
remain outside Git; black protected video surfaces were not treated as proof
of playback. No shared daemon was restarted. The targeted suite passes 61 tests, including
Twitch stop-before-launch, Apple URL/query handling, Prime GTI validation,
failed acknowledgments and Spotify exact-device targeting; Python syntax and both changed skills validate. The
updated TV skill/reference and Spotify targeting note were deployed atomically
and compared byte-for-byte with source. The installed developer CLI resolves
to the tested source. Full natural-language OpenClaw dispatch remains untested;
no gateway restart was required.

## Host setup and recovery

The MacBook has Rosetta and the official Intel SDB 4.2.36 binary at
`~/.local/share/samsung-tv-tools/sdb-4.2.36/sdb`, with adjacent provenance JSON.
Only SDB was extracted; the SDK installer was not run.

- [Official archive](https://download.tizen.org/sdk/tizenstudio/official/binary/sdb_4.2.36_macos-64.zip)
- [Official package manifest](https://download.tizen.org/sdk/tizenstudio/official/pkg_list_macos-64)
- Archive SHA256: `0bd307ac8382c857be5c2106dd5b8587b10c1c91ac1e504de8432c8276bc28c5`
- Binary SHA256: `05db410d2e36e85147f95b20bae6296a71c54dab0e1ec79f9e11b89c4b6bd2cd`

Developer Mode required a full mains power cycle before port 26101 opened.
Arrange power cycling with the owner and never interrupt an update. Check the
MacBook's current LAN address against the TV's Developer IP after network changes.
The ordinary OpenClaw WebSocket permission is separate from Developer Mode.
Disabling firmware auto-update is not a prerequisite; reenabling it was recommended
but has not been confirmed.

## CleanFrame reference and next steps

[CleanFrame's setup guide](https://github.com/nthnclrk/CleanFrame/blob/main/docs/agent-tv-setup-guide.md)
corroborates the developer connection and full power-cycle requirement. Its
reference TV is a 2024 LS03D running Tizen 9, so its results do not establish
compatibility with this LS03B/Tizen 6.5 TV.

A separately signed diagnostic/control web app is a possible next step, using
the owner's Samsung account, certificates, device authorization and a new app
identity. Installation would need agreement on that scope. CleanFrame's optional
native Home reassignment uses an undocumented execution workaround and native
settings writes; it is neither needed nor authorized for our browser-control task.

The 2026-10-24 tests below confirmed affected-chain acceptance in both the
built-in browser (owner-observed checker results) and the native curl client.
Neither result establishes the trust behavior of every other TV application.
Failed requests alone would not prove certificate rejection.

See Samsung's [device connection guide](https://developer.samsung.com/smarttv/develop/getting-started/using-sdk/tv-device.html)
and [debugging guide](https://developer.samsung.com/smarttv/develop/extension-libraries/smart-view-sdk/receiver-apps/debugging.html).

### QN90B research reference

The owner's additional [QN90B research reference](https://github.com/chris-ritsen/samsung-tv-root/blob/master/docs/research-notes/QN90B_REPRODUCTION.md)
is a closer platform match: T-PTMAKUC, Tizen 6.5 and ARMv7 match our observed
values. Its owner-tested TV is QN55QN90BAFXZA on 1602.3, not this Frame on
1720.7. The diagnostic below confirmed Linux 5.4.77; memory-layout compatibility
and root-exploit prerequisites remain unverified and were not needed for navigation.

Static review of the current README, quick start, `qn90b.py` and `sdb.py` found:

- The compatibility preflight uses package-installer command injection as the
  SDK user and can stage a temporary script. It is not merely an SDB metadata
  query, although it does not invoke the root payload.
- The QN90B root route writes its process credentials through a physical-memory
  mapping. The separate UEP control has an exact-build guard; matching Tizen
  version or firmware family is insufficient to use its fixed address.
- The newer archive route is an alternative worth reviewing before considering
  kernel-memory writes. Its `probe` actually acquires root and cleans up; it is
  not a read-only check. No root route has been executed on this TV.
- Upstream QN90B capabilities include source selection, volume, display control
  and native events. This does not establish Frame Art Mode, browser navigation,
  screenshots, or certificate trust on our model. Our later local observations
  of browser navigation and certificate acceptance are recorded separately below.

### Authorized SDK-user diagnostic and navigation result

On 2026-10-24 the owner authorized a bounded diagnostic through the documented
SDB package-name command-injection path, specifically to test deeper access and
browser navigation without kernel changes. Observed directly:

| Check | Result |
| --- | --- |
| Identity | UID 901; SMACK label `System` |
| Kernel / architecture | Linux 5.4.77 / armv7l |
| Build | `T-PTMAKUC-REL-202602250225` |
| Release | `Tizen6/TV 6.5.0 (arm)` |
| Browser application | `com.samsung.tv.knox-browser` |
| Manifest | Explicit HTTP/HTTPS `view` app-control handlers |
| Native launch | `launch_app` acknowledged success, exit 0 |
| Navigation proof | TV fetched a nonce-specific local page and executed its JavaScript beacon |
| Visible result | Owner confirmed “TV navigation verified” on screen |
| Browser user-agent | SamsungBrowser/2.2rab, Chrome/108.0.5359.1, Tizen 6.5 |
| Cleanup | Each temporary diagnostic script/gate removed; owned SDB and test HTTP listeners stopped |

The relevant installed-library keys were verified before this native command was
run as the SDK user:

```sh
launch_app com.samsung.tv.knox-browser \
  __APP_SVC_OP_TYPE__ http://tizen.org/appcontrol/operation/view \
  __APP_SVC_URI__ 'https://emudhra.we-have-a-problem.com/'
```

This command belongs inside the authorized SDK-user execution path; ordinary
`sdb shell` still cannot run it. The normal `samsung-tv-dev browser` command
only launches the app; `samsung-tv-dev open URL` now exposes the verified
navigation path with argument validation and bounded cleanup.
The diagnostic used reviewed temporary host scripts rather than installing the
upstream root toolkit. It bound callbacks to the MacBook's LAN address, checked
the TV's peer address and a per-run nonce, bounded output/time, and checked UID
901 before running the diagnostic body. The SDB transport returned exit 1 for
the synthetic package name even when the diagnostic succeeded; callback proof,
native-launch output and observed browser behavior established success.

The checker was subsequently opened using this native path; the owner confirmed
it was visible and reported **“All Red Failures”** after running the check.
The checker describes successful connections to its affected-chain hosts as
vulnerable results. This is owner-observed browser evidence, not a machine-captured
per-row result export. The local navigation page separately proves control.
The user-agent is reported evidence, not proof of the browser's patch coverage.
No root payload, UEP change, kernel write, persistent controller, app installation
or Home reassignment was performed.

### OpenClaw control skill deployment

The owner requested a reusable OpenClaw skill for app launches and specific
content. The tracked source is `openclaw/skills/samsung-tv/`, with app-specific
routing guidance in `references/content.md`. It is deployed as a real copy to
`~/.openclaw/skills/samsung-tv`; `/opt/homebrew/bin/samsung-tv-dev` links to the
tracked helper. The active gateway reports the skill **eligible and model-visible**
with no missing requirements. No gateway restart was needed.

The developer/content helper runs on the Crosstown MacBook over SSH. The
Mini-side `samsung-tv` CLI sends REST, WebSocket keys, volume, and Art Mode
connections through SSH forwards on the same MacBook. Wake broadcasts also
originate on the Crosstown LAN; TV control does not depend on the other
workstation's subnet route.
The standard install process already links all tracked `bin/` files and copies
the skill tree, so this uses the existing deployment contract.

Live-tested helper operations: app discovery, browser manifest inspection,
YouTube public/native app metadata, native-player running state, app/URI launch,
and YouTube playback/requested-video state. Nineteen targeted tests cover owned SDB
cleanup, callback proof, input quoting/validation, manifest projection, and
YouTube session isolation and output redaction. Wake-from-off, other streaming
apps' content routes and individual remote-key reactions were not exercised.

For the owner's public video `eyZEqSPeMwg`, native app launch with a view URI
opened YouTube but did not select the video. DIAL with `v=VIDEO_ID` also left
the home screen displayed. The dedicated `youtube` command instead obtains
an ephemeral screen-control session and submits one `setPlaylist` request.
The service accepted that request. A subsequent read-only `getNowPlaying` check
reported the exact requested video, state `1` (playing), and advancing time
(419.883 → 420.349 seconds). The owner then reported that the TV still showed
the account-selection screen. This invalidated the earlier claim of confirmed
visible playback: API state can disagree with foreground UI. The reusable
`youtube-state VIDEO_ID` command now labels this as
`requested_video_playing_in_api`, with `playback_verified`, `screen_observed`
and `profile_selection_verified` false. It does not restart playback or start
YouTube when stopped. A regression test preserves that distinction.

The owner identified Dylan as the highlighted profile and requested its
selection. One `KEY_ENTER` was sent; the owner confirmed that the chooser closed
and YouTube's home screen appeared. A fresh request for the original video was
then sent after that profile-selection change. The owner confirmed: “the video
is now playing.” This establishes attended playback in the YouTube app after
selecting Dylan, while preserving the API-only verification limitation.
The helper does not equate acceptance with playback and never uses a browser
fallback. No session token or screen identifier is persisted in the repository
or the helper's output.

### Netflix attended control test

On 2026-10-24 the owner requested Netflix testing and delegated the title choice.
Installed-app discovery returned `org.tizen.netflix-app`; the manifest advertised
`.netflix.com` DIAL support but no standard URI handlers. SDB launch was
acknowledged (PID 4553), and the owner confirmed the profile chooser appeared.
The owner identified Dylan as highlighted; one `KEY_ENTER` selected it, and the
owner confirmed Netflix's home screen appeared.

Local DIAL reported version `70.23.15090`, changing from stopped to running.
For the official *Our Planet* title ID `80049832`, two bounded content-launch
tests used the DIAL Netflix endpoint with the Netflix Origin and text/plain body:
JSON `{"v":"80049832"}`, then form `v=80049832`. Both returned HTTP 200;
the owner reported no title selection or playback and explicitly confirmed the
screen was unchanged after the second request. No further automatic retries
were made, no browser fallback was used, and Netflix was left on its home screen.

Verified: native app launch and profile selection with the remote Enter key.
Unresolved: direct title selection and actual Netflix playback/transport controls.
The deployed skill records these limits; no unsupported Netflix playback shortcut
was added. Further title control requires observed UI navigation or investigation
of Netflix's app-specific interface, not inference from its DIAL response.

The owner subsequently reported the TV was off and requested a fresh retest.
LAN wake from the Crosstown Mac succeeded to the extent that REST reported
power on, developer connectivity returned, and the owner confirmed Netflix's
home screen after a new SDB launch. A bounded WebSocket `DEEP_LINK` request
to `org.tizen.netflix-app` with metaTag `80049832` was sent; the owner again
reported Netflix home. Thus this exact deep-link request also failed to establish
title selection. No screen capture or foreground UI inspection was available;
the user-facing explanation explicitly distinguishes app/player telemetry from
seeing the screen. Other app-ID or payload variants remain untested.

### Foreground and screen-inspection investigation

On 2026-10-24 the SDK account successfully queried
`aul_window get_focused_pid` and mapped the result with
`aul_test get_app_bypid PID`. Netflix owned focused PID 4553. The reusable
`samsung-tv-dev foreground` command now checks focus before and after mapping
and reports the app only when those observations agree. It does not launch an
app or send remote keys. Its live result verified `org.tizen.netflix-app`, while
explicitly leaving screen observation, UI-tree availability and playback false.
Window-stack metadata was also accessible, but supplies window/PID/visibility
information rather than labels or selected tiles.

Screen/UI paths checked on this exact firmware:

| Path | Observed result | Meaning |
| --- | --- | --- |
| `enlightenment_info`, including `winfo` alias | root:root, mode 0550; permission denied | Not callable by SDK UID 901 |
| Compositor D-Bus introspection | Explicit XML security-policy denial | No supported inspection through that account |
| `/dev/fb0`, `/dev/fb1` | Absent | No conventional framebuffer screenshot path established |
| AT-SPI accessibility | Libraries present; no system-bus owner, SDK session-bus address, or matching accessibility process observed | No usable accessibility tree established; not proof that firmware lacks accessibility |
| `vd-nui-objectdump` | Requested a dump for focused PID 4553, but returned no tree or labels | Request acceptance is not successful UI inspection |
| `dumpsys` | Usage requires a service name; no working UI service identified | Unverified |

Samsung's [screenshot reference](https://github.com/Samsung/tizen-agent-skills/blob/main/tizen-sdk-skills/common/skills/tizen-screenshot/SKILL.md)
uses root for the compositor tool; its emulator-host capture alternatives do
not establish capture of a physical retail TV. The researched
[root project's capture implementation](https://github.com/chris-ritsen/samsung-tv-root/blob/master/src/samsung_tv_root/capture.py)
targets QN90F retained HDMI frames. That does not establish native Netflix UI
capture on this LS03B. Root access would therefore remain a separate decision
and would not by itself prove that a usable screenshot can be obtained.
No root acquisition, permission/security-policy changes, hardware-composer
changes, Voice Guide activation, app restarts, or persistent capture services
were performed during this investigation.

Use focused-app inspection plus the existing player-specific telemetry now.
Profile/highlight/title-page verification still needs an actual screen view.
Further UI-tree research should first establish a documented accessible service
for this firmware; do not treat installed library names as a working interface.

The investigation also exposed a staging cleanup edge case: the synthetic
package command can be evaluated again after the script removes itself, recreating
an empty guard directory. The helper now checks that its script exists before
creating the guard. A regression test runs the command twice and verifies both
paths remain absent. Foreground parsing tests reject changed focus and mismatched
PID mappings, and preserve the distinction between app focus and visible playback.

### Owner-authorized temporary root and working screen capture

After the SDK-only investigation, the owner explicitly requested establishing
root for UI inspection. The archive-launcher route succeeded on this exact
LS03B/1720.7 firmware. Root callbacks authenticated and reported UID/EUID/GID/EGID
0, with SMACK label `User`. It ran the previously restricted compositor tool.
This supersedes the earlier "root not attempted" status above.

The reviewed route uses a private mount namespace and temporary bind-mounted
views for `/etc/passwd` and the SDK verification key. It does not overwrite those
underlying files. Both hashes matched before/after each successful session;
payload staging was removed and the owned host SDB listener stopped. No kernel
memory write, UEP/SMACK policy change, Home reassignment, hardware-composer toggle,
or persistent TV root service was needed.

`enlightenment_info -dump topvwins DIRECTORY` produced actual PNGs. The focused
Netflix surface was retrieved and visually inspected at 1920×1080: first an idle
*One Punch Man* promotional screen, then—after one `KEY_UP`—Netflix Home with
readable navigation, title text, and a highlighted Play button. This establishes
usable UI pixels, not Netflix title selection or protected-video playback.
Only the requested app image was transferred; other generated window buffers
were removed with the per-run capture directory.

The reusable command is now `samsung-tv-dev screenshot --output NEW_LOCAL_PNG`.
It refuses an existing output file, writes mode 0600, pins its root assets,
checks root/cleanup evidence, selects the focused window's PNG from the bounded
capture directory, verifies focus again, and checks the image transfer digest.
It exposes no arbitrary root-shell command. No screenshot is stored in Git.

Controller assets on the Crosstown Mac:

- `~/.local/share/samsung-tv-tools/archive-root-0.0.12/`: five reviewed Python
  source files plus the two .NET 3.1 payload assemblies and a pinned manifest.
  No global Python/.NET installation or persistent host controller was added.
- [Upstream v0.0.12 release](https://github.com/chris-ritsen/samsung-tv-root/releases/tag/v0.0.12),
  macOS ARM64 archive SHA-256:
  `8ee4587462963f8ad0374f921e41050bd9198a7cb0b15899a5f35a7ad8f50f2c`.
- Manifest SHA-256:
  `72e3db26a3f03b28bec69050029f93a0b948c922c1104f31137354f63274736f`.
- Source package files: `__init__.py`, `archive_root.py`, `root_agent.py`,
  `resources.py`, `sdb.py`. Payloads: `SamsungTvArchiveRoot.dll` and
  `SamsungTvRootAgent.dll` from `_internal/payloads/common31` in that archive.
  The helper verifies the manifest and each listed file before importing them.

The standalone executable's SDK callback timed out before establishing root;
the cause was not conclusively identified. The reviewed source works under the
Mac's existing Python 3.9 using our nonce/peer-checked SDK transport, so no firewall
permission changes were needed. An initial adapter newline-format error caused
a false cleanup failure after a successful root callback; this was corrected,
then the session repeated with both hash and staging checks passing.
Direct SDB pull did not retrieve the screenshot; the helper instead transfers
PNG data over the existing bounded callback and verifies its SHA-256.

Capture is a point-in-time app-surface image, not a semantic accessibility tree
or guaranteed full display composite. Use it to observe menus before sending
short key sequences. Ordinary app launch remains unprivileged; root is acquired
only for a bounded screenshot request and exits afterward.

### Netflix playback and native content handler investigation

The owner confirmed native Netflix playback on 2026-10-24. Captures of the
player controls identified *Our Planet*, S1E1 **One Planet**, with advancing
elapsed time (03:49, 13:21, 15:15, 19:10) and the Pause control. Protected video
was black in the app-surface capture, while controls and subtitles remained
readable. Visible video was confirmed by the owner, not inferred from that black
surface. Observed Search → series → Episodes navigation selected the episode.
The skill's content reference records the layout and cross-season carousel.

`bin/samsung-tv keys frame KEY...` now batches 1–16 navigation/transport keys
through one WebSocket connection with 0.35-second spacing. Invalid inputs never
connect; a partially sent batch reports its send count, closes, and is not
retried. Power/mute toggles are excluded. Forty-four targeted Samsung tests
pass, including batch ownership, partial-send behavior, exact Netflix URL
validation, bounded stop/wait/launch, unknown-state rejection and no launch retry,
plus public title/episode identity, ambiguity, metadata failure, and fetch limits.

For transient player controls, the diagnostic waited until temporary root was
ready, signalled the Mini to send one UP key, and captured four seconds later.
Sending UP before the entire root setup can let the controls disappear before
the capture. This diagnostic timing hook is not yet a public helper command.

#### What the installed code actually handles

Inspection covered static application/launcher files, public Netflix metadata,
and scoped operational diagnostics. Narrow process reads inspected only known
launch-state pointers, flags, a dynamic function binding and an exact match for
our submitted public content query. No account database, cookie store, DRM
material or general process-memory dump was read. Static binary copies and screenshots
remain outside Git in temporary directories.

- `/opt/usr/apps/org.tizen.netflix-app/bin/netflix-app` is an 8,208-byte loader.
  It opens Samsung PVOD's `shared/lib/libpvod-plugin.so.1` and calls `main_emp`.
- That 361,286-byte plugin exports `LaunchInfo::setLaunchInfo`,
  `SetDataFromAppControlHandle`, `GetQparameter`, and the app's `OnControl` handler.
  Thumb disassembly shows `action_play_url` is separate from `PAYLOAD`: the
  former becomes a raw query suffix; the latter is URL-encoded into `dial=`.
  `NETFLIX_SOURCE_TYPE` is parsed as an integer. A running-app branch constructs
  a source-type command and passes it to Netflix's `sendSystemCommand`.
- Netflix's 700,144-byte adapter contains `NETFLIX_LAUNCH` and `deepLink` in its
  Samsung content-tile construction. These strings alone are not a callable API.
  It also references optional `launchParams.txt` / `launchParamsExtra.txt`; neither
  was present in the inspected resource directory.
- The 45,828,396-byte Gibbon library contains `deepLink`, `movieId`, and a
  system-command-to-JavaScript message. This suggests the final interpretation
  occurs in the Netflix UI runtime. The successful startup payload is recorded below.

Static SHA-256 provenance:

| File | SHA-256 |
| --- | --- |
| Netflix launcher | `dc6dc24292c42220cb5679313380b239c0cef5833f17027a6f46fdf2a72249e4` |
| Samsung PVOD plugin | `74f11a7cb236738a5c3965edc7c6a22d33674a0058cfb6453dba4b95e3201fe7` |
| Netflix adapter | `4b149ee1aa1ed0663d503989729097d2372a640ed75f8be1c373a645ded1826b` |

DIAL `m=<Netflix watch URL>&source_type=4`, native `eden_resume` with a PAYLOAD
values object, and native `action_play_url` with either `movieid=80094031` or
`m=https://www.netflix.com/watch/80094031&source_type=4` all acknowledged requests
without switching from S1E1 to S1E2. See the content reference for exact tested
combinations. They are failed behavioral tests, not supported fallbacks.

#### Confirmed cause and working direct episode route

The installed `launch_app` binary accepts ordinary key/value pairs. A captured
[Samsung Smart Hub payload in Yandex RTV](https://github.com/yandex/rtv/blob/c387c23aa6b3826993ee930a0dac0950dfbf2bd7/packages/server/test/mocks/tizen24/eden-apps.json)
established numeric `m=ID`, `trackId` and `showMovieInfoOnly`, rather than an
ordinary watch URL. Numeric warm requests also failed.

A read of the native LaunchInfo fields confirmed our exact
`&m=80094031&showMovieInfoOnly=false` query and source type 4, with its dispatch
flag consumed. The running PVOD plugin's jump slot at relative address `0x67160`
resolved to the Netflix adapter's Thumb function at relative address `0x25c8c`.
That 14-byte `IAppManagerContext::sendSystemCommand` implementation simply
returns zero. This establishes why the warm content-dispatch path produces no
content change on this installed build. Recheck file hashes and runtime bindings
after app/firmware updates; these offsets are diagnostic evidence, not code to
hard-code into routine controls.

A fresh process takes a different startup path. The owner confirmed **Frozen
Worlds (S1E2, 80094031)** playing after graceful termination and this launch:

```sh
/usr/bin/launch_app org.tizen.netflix-app \
  action_play_url 'm=80094031&trackId=15539201&showMovieInfoOnly=false' \
  NETFLIX_SOURCE_TYPE 4
```

The durable command is `samsung-tv-dev netflix CONTENT_URL_OR_ID`. It checks
installation and running state, gracefully terminates Netflix only when running,
waits for a recognized stopped state with a bounded poll, and submits one native
launch. It does not force-kill Netflix or retry a failed launch. Routine requests
use SDK UID 901, with no root, binary modifications, browser fallback or keyboard
search. A second test through this helper selected **Jungles (S1E3, 80094030)**;
the owner confirmed that episode playing. The helper returns launch/request
acknowledgment separately from actual playback observation.

Netflix's public metadata explicitly maps seasons and episode numbers to IDs.
One Planet is `80094026`, Frozen Worlds is `80094031`, and Jungles is `80094030`.
Sorting IDs numerically would produce the wrong ordering. A series ID can leave
Netflix to choose an episode; use an episode watch ID for an exact request.
Netflix controls profile/entitlement and saved progress. The command can briefly
interrupt playback because it restarts the app; it is not a status probe.

#### Diagnostic cleanup and session limits

Scoped dlog checks returned no launch evidence. Temporarily setting
`enable_apps` from 0 to 1 also yielded no usable app trace; the original config
was copied back and `enable_apps=0` independently rechecked. No logging or Netflix
binary change remains. The final root session reported unchanged protected system
files, removed root staging and confirmed shutdown of its owned host SDB server.

A temporary multi-request diagnostic session exposed two constraints: the
initial SDK launcher transport must stay open until root exits, and further SDK
injections can block while that launcher is active. Its first short transport
timeout caused cleanup warnings; both named callback scripts and guards were
subsequently confirmed absent. Root-protocol file reads closed the tested
connection, leaving one generated screenshot directory; that exact directory
was removed and absence confirmed. The working diagnostic instead used root EXEC
with base64 image transfer and kept the initial transport alive. Generated
capture directories were removed after each successful capture. These temporary
session changes were not shipped as a persistent service. Ordinary one-shot
`screenshot` retains its existing bounded workflow.

#### Persistent root availability

The owner requested lasting root availability for OpenClaw. Current screenshots
reacquire root automatically for each request and clean up afterward; there is
no permanent root session or TV boot service. Upstream v0.0.12's controller uses
separate model-specific root methods; the working archive-root probe is not a
drop-in controller backend. A host-side controller using this verified archive
route remains a separate implementation item. Do not deploy a kernel-writing
backend just to retain the access already working here.

### Native TLS acceptance check

The same bounded SDK-user path ran the TV's stock curl 7.73.0, linked to
OpenSSL 1.1.1w, with normal certificate verification (no `-k`, custom CA file,
or trust-store changes). Each request had a seven-second limit and discarded
the response body. Results:

| HTTPS host | Purpose | HTTP | Verify result | curl exit |
| --- | --- | --- | --- | --- |
| `emudhra.we-have-a-problem.com` | Normal HTTPS control | 200 | 0 | 0 |
| `dv-g3b-emudhra.we-have-a-problem.com` | Affected intermediate chain | 200 | 0 | 0 |
| `dv-g3b-noct-emudhra.we-have-a-problem.com` | Affected chain, unlogged leaf | 200 | 0 | 0 |

This confirms affected-chain acceptance in that native client, including the
No CT variant. It is a two-canary diagnostic, not a complete native-client
matrix. Browser acceptance was separately observed by the owner; neither test
implies exploitation has occurred. These are current unmitigated findings.

Next remediation work should establish whether Samsung supplies a supported
firmware/trust update for the exact US model and verify behavior after updating.
Until resolved, avoid sensitive browsing on the built-in browser. Any wider
network restriction must account for streaming and SmartThings use. Rooting
or replacing a certificate file would require a separate plan, because browser
and application trust may differ and updates may overwrite local changes.

## HBO Max / Peacock helper validation

The expanded TV suite passes 70 offline tests, including intact native payloads,
positive-PID acknowledgment, failed-stop/no-retry handling, exact provider URLs,
episode/series separation, and Peacock's two decoding passes with punctuation.
Python compilation and skill validation pass. Updated skill entrypoint, app
reference and lookup guidance are deployed atomically and byte-verified; the
Mini's CLI symlink uses the tracked helper directly. The final Peacock payload
adds a second encoding layer for title punctuation; that layer is decoder-tested
but has not yet been exercised in another live request.

## Performance and advertising assessment (2026-10-24)

The owner wants less advertising and better responsiveness, not storage recovery,
and does not use Samsung TV Plus, personalized recommendations or built-in voice
assistants. The initial assessment inspected the TV without changing preferences, registry
flags, services, application state or Home routing. Temporary root staging and
owned SDB cleanup were verified.

Two process-counter samples, approximately 3.51 and 8.47 seconds, showed 82.1%
and 88.3% aggregate CPU idle respectively. `CLK_TCK` is **250**, not 100; do not
interpret raw process ticks with a desktop default. The second sample had 0.02%
I/O wait. These are short observations during the existing Peacock session,
not a controlled menu-latency benchmark. Available memory was about 423 MiB;
compressed swap was nearly full, which alone does not prove current thrashing.
Spotify remained resident at about 126.5 MiB RSS and Bixby's capsule viewer at
43.8 MiB RSS. RSS includes shared pages and cannot be summed as guaranteed savings.

Read-only checks found concrete first-pass candidates:

| Preference | Observed type/value | Proposed change |
| --- | --- | --- |
| `db/was/preload_by_country` | String `true` | String `false`; verify after catalog refresh/reboot |
| `db/menu/autorun_tv_plus` | Boolean True | Boolean false |
| `db/si_service/voice_recog_enable` | Boolean True | Boolean false |
| `db/multi-assistant/prelaunch_mode` | Boolean True | Boolean false |
| `db/menu/voice/wakeupless_status` | Int32 0 | Already off; no change |
| `db/menu/voice/voice_activated` | Absent | Do not create a foreign-firmware key |

Viewing Information Services/interest-based advertising consent had not yet been
inspected during this initial assessment. The subsequent opt-out and verification
are recorded below. Opting out of personalization does not establish an ad-free
Home screen. Use the same menu/workload for controlled latency comparisons.

The installed units confirm negated `ConditionPathExists` stop markers for
`pisa-service.service` (`pisa-service`), `canalysis-service.service`
(`canalysis-daemon`), and the context-aware agent/service (their matching names).
All four markers under `/opt/usr/share/systemd/system/` were absent. These are
candidates for separate reversible tests, not a tested disable set. PISA has
path/socket triggers and multiple units restart automatically; killing their
processes alone is not durable. `data-service.service` is required by the
context-aware service. Do not blindly import a runtime mask list. The media
analytics unit was already failed/inactive with no restart loop observed.

[Quiet Tizen's profile](https://github.com/jss8649/quiet-tizen/blob/main/profiles/un75u8000ffxza-1300.9.json)
is for UN75U8000FFXZA / Tizen 9.0 / .NET 6.0.9, not this LS03B / Tizen 6.5 /
.NET 3.1 setup. Of its application IDs, this inventory contains 8/10 advertising,
17/17 voice, and 7/13 context targets. Its TV Plus ID is absent; this TV uses
`com.samsung.tv.tvplus` and `com.samsung.tv.tvPlusDeeplinker`. Its Bixby-provisioning
and voice-client unit names are absent too. The local package registry has the
expected TEXT `app_disable`, `app_onboot`, and `app_autorestart` fields. Selected
rows use `true`/`false`; ACR/canalysis permit auto-restart and targeted-ad-service
permits both boot start and auto-restart.

The [cleanup planner](https://github.com/jss8649/quiet-tizen/blob/main/tools/plan_cleanup.py)
provides a useful pattern: consistent SQLite snapshot, exact row/field guards,
transactional changes and field-specific restore. It does not itself enforce
actual TV model/firmware, stop running processes, apply preferences or prove
cross-reboot behavior. Any future registry plan needs this TV's fresh snapshot
and a reviewed target list; do not apply the supplied profile wholesale.

Preserve Art Mode/ambient components, SmartThings/remote and wake integration,
HDMI/audio/network/Bluetooth, accessibility/TTS, app/account managers, the
current Home/settings/browser, and the requested streaming apps. The
[earlier debloat guide's protected-package list](https://github.com/ardazeytin/samsung-tizen-tv-debloat#part-5--never-remove-these)
is useful as a deletion exclusion; its Tizen 4.0 access limitations do not describe
our verified root route. No platform-package deletion is proposed. Existing
app-preload preferences can be restored by Samsung catalog refreshes, as
[Quiet Tizen documents](https://github.com/jss8649/quiet-tizen/blob/main/docs/03-cleanup.md);
no enforcement daemon or boot modification has been added.

## Applied privacy and performance changes (2026-10-24)

The owner authorized this sequence: opt out of Viewing Information Services and
interest-based advertising, stop PISA/content analysis using their verified
markers, then disable TV Plus autorun, voice recognition and assistant prelaunch.

The normal settings overlay closed before navigation completed. Opening the
installed `tv6pbVMQFF.PrivacyChoices` app and selecting its Terms & Conditions /
Privacy Policy entry opened `org.tizen.wizard`. Both optional consent boxes were
initially checked. Only those two boxes were unchecked; the owner reviewed the
terms and selected OK. A fresh Privacy Choices instance and reopened consent
page visibly confirmed both remained unchecked. A warm developer launch had
acknowledged the request without bringing the page forward, so the verification
used a graceful restart of the Privacy Choices app only.

Applied and immediately verified:

| Target | Result |
| --- | --- |
| Viewing Information Services | Unchecked after save and reopening |
| Interest-Based Advertisements | Unchecked after save and reopening |
| `pisa-service.service` | Inactive, MainPID 0; explicit start skipped with ConditionResult=no |
| `canalysis-service.service` | Inactive, MainPID 0; explicit start skipped with ConditionResult=no |
| `db/menu/autorun_tv_plus` | Boolean True → False |
| `db/si_service/voice_recog_enable` | Boolean True → False |
| `db/multi-assistant/prelaunch_mode` | Boolean True → False |

The marker files are `/opt/usr/share/systemd/system/pisa-service` and
`/opt/usr/share/systemd/system/canalysis-daemon`. Both are empty, mode 0644,
numeric UID/GID 0, with Smack floor label `_`, matching readable vendor condition
markers. Only the new marker files were labeled; no Smack policy was changed.
The original service-unit hashes still match. This change does not mask the
separate `contents-recognition-service.service` or change package registry flags.

Two firmware behaviors required adapting the operational checks:

- PISA exits with status 255 on stop. After confirming MainPID 0, clear only that
  unit's stop-failure state, then test the vendor start guard. The first tests
  were rolled back and PISA was independently observed running again before the
  final application. The initial private `User`-labeled marker was replaced by
  the readable marker described above; its label alone did not resolve the stop
  exit-status handling.
- Content analysis did not finish on SIGTERM within systemd's existing 90-second
  stop timeout. The first short verification window requested rollback, and the
  service was observed active again afterward. The final change issued a normal
  asynchronous stop and waited outside the temporary root session. Systemd used
  its existing timeout behavior (ExecMainStatus 9, Result=timeout). After PID 0
  was verified, its failure state was cleared and a fresh start request was
  blocked by the marker. No global stop timeout or kill policy was changed.

Rollback records are stored privately on both machines:

- TV: `/opt/usr/share/openclaw-tv-backups/20261024-privacy-performance/`.
  The final phases are `pisa-final`, `canalysis-final`, and `preferences`, each
  with `restore.sh` and its before-state records. Earlier attempt records are
  retained separately; use the final phase records for the applied state.
- Mini: `~/.local/share/samsung-tv-tools/changes/20261024-privacy-performance/`.
  This includes the pinned-asset root runner, reviewed per-phase apply scripts,
  private consent captures, verification results and progress record.

The TV rejects executing the saved unsigned shell script by pathname. Feed the
reviewed restore file through standard input (`/bin/bash < PATH/restore.sh`)
inside the authorized root session instead. The root runner similarly sends a
base64-encoded script through stdin to preserve literal quoting/backslashes.
PISA restoration through this route was exercised successfully. Preferences
restore the three recorded Boolean values; service restoration removes only its
new empty marker and restarts the service only if it was originally active.
Verify actual service state afterward because a nonblocking start is not proof.
Do not restore a whole database or use an earlier attempt's snapshot over later
changes. Privacy consent restoration, if requested, belongs in the same visible
UI with owner review of any terms confirmation.

Background app preloading, context services, Home routing, Art Mode and the
streaming app packages were outside this selected phase. A Home request was sent
for the owner's usability check. No controlled before/after latency improvement
was established in this phase. The later full restart verified the targeted
app flags and stop markers; see the current maintenance summary above.
Disabling personalization does not guarantee removal
of all sponsored Home content.

A fresh root health check after the Home request confirmed both services remained
inactive (MainPID 0, ConditionResult=no, NRestarts 0), all three preferences still
read False, and PISA's path activator was waiting normally. The TV REST endpoint
still reported power on. Twenty-three final rollback-record files were copied
to the Mini; all three restore-script hashes matched the TV originals. Temporary
root staging and the owned host SDB listener were cleaned up. The owner's
subjective usability check was completed separately, as recorded below.

An additional responsiveness check sampled process counters for 8.51 seconds
after the Home request. Aggregate CPU idle time was 72.08%, I/O wait 0.01%,
and available memory 309,368 KiB (about 302 MiB). Both disabled services remained
inactive with MainPID 0, ConditionResult=no, Result=success and NRestarts 0.
The SDK diagnostic and its owned host SDB listener were cleaned up afterward;
the private sample is retained with the change records as `post-change-home.txt`.

This sample does not establish a speed improvement. The earlier 8.47-second
sample had 88.26% CPU idle time and 433,528 KiB available memory, under a
different foreground/background workload. Neither sample measured visible
navigation latency, and the sampling shell itself used CPU. Nearly full logical
zram also does not, by itself, establish active swap thrashing. Automated inputs
were stopped for the owner's comparison of Home/Settings and streaming-app
navigation. The owner reported **“About the same”**: no noticeable improvement
was established. Treat this phase as verified privacy/background-service
reduction, not a demonstrated responsiveness fix. No privacy settings were
reenabled to create an artificial comparison. The owner deferred a restart during
this phase, then authorized the later full restart documented above. The same
subjective navigation comparison has not yet been repeated after that restart.

### Owner-confirmed integration checks after cleanup

On 2026-10-24, the owner successfully lowered TV volume by one through Google
Home and confirmed the SmartThings iOS app still worked. This establishes those
observed control paths after the changes; it does not establish every app
function, remote wake, Art Mode behavior, or persistence after a full restart.
The owner subsequently verified that SmartThings iOS can both pause and resume
Netflix. Google failed on phrases using "the TV" and "pause Netflix". With the
exact device name, Google recognized Samsung The Frame 65 but explicitly said it
could not pause media playback on the TV. Simple name ambiguity did not explain
that result. The owner then disabled iPhone Wi-Fi and verified both pause and
resume over cellular, establishing SmartThings cloud playback control as well.
The owner then verified that Google Home's iOS playback buttons also pause and
resume Netflix. Google device sync completed, but exact-name voice commands
still failed. The observed failure is therefore specific to the voice-control
path; playback control through the Google Home app and SmartThings cloud works.
The exact voice-assistant backend cause has not been established.

Google also reported no media playing while the owner reported Netflix playing
on the TV. The owner had not previously verified that capability, so its status
is unresolved, not a confirmed cleanup regression. Samsung documents generic
Google Home play/pause commands through SmartThings, while Netflix documents
Google Assistant control for Chromecast and selected Google Cast TVs. Neither
establishes native Netflix playback-state reporting on this exact Frame. Keep
device controls, media transport commands, and now-playing metadata as separate
checks; do not reenable content recognition merely to expose playback state.

References: [Samsung Google Home commands](https://www.samsung.com/za/support/apps-services/control-your-tv-or-soundbar-by-connecting-google-home-bixby-or-alexa/)
and [Netflix Google Home support](https://help.netflix.com/en/node/55587).

Read-only follow-up found the TV powered on over Wi-Fi and Netflix as its stable
foreground app. The local UPnP AVTransport service advertised Play/Pause but
reported `NO_MEDIA_PRESENT`, zero tracks, no current URI/metadata and no current
transport actions. This describes the UPnP renderer, not Netflix's internal
player or SmartThings' cloud state; do not use it to infer Netflix is stopped or
that Google queries this endpoint. No playback commands were sent by these
diagnostics.

SmartThings explicitly documents device-named Google pause/play commands in its
[integration guide](https://blog.smartthings.com/partner-spotlight/try-this-control-your-samsung-tv-with-hey-google/).
Google models [transport commands](https://developers.home.google.com/cloud-to-cloud/traits/transportcontrol)
separately from media state. Google's raw advertised traits have not been read,
but the successful Google Home app playback test means we must not describe
pause/resume as generally unavailable through its integration. Resync did not
resolve voice control. Reconnecting a working account is not the next step.
A candidate workaround is a uniquely named voice-triggered automation with a
direct `device.command.MediaPause` or `device.command.MediaResume` action,
rather than an assistant custom command repeating the failing phrase. Google
documents these actions, but the editor must accept this specific TV and the
automation must be tested before claiming it works. No automation was created.
References: [MediaPause](https://developers.home.google.com/automations/schema/reference/entity/sht_device/media_pause_command),
[MediaResume](https://developers.home.google.com/automations/schema/reference/entity/sht_device/media_resume_command),
and [voice starter](https://developers.home.google.com/automations/schema/reference/entity/assistant/ok_google_event).
No account
relink, device removal, router change, firmware update, consent change or TV
restart was performed. No existing SmartThings API client was found in the
scoped control tooling, so the account's actual cloud capabilities/state remain
uninspected. Google Home's preferred-video-device setting is documented for
Cast-compatible devices; it is not an established fix for this SmartThings TV.

### Further background-service review (2026-10-24)

The subsequent [privacy and dependency catalogue](samsung-tv-privacy-catalogue.md)
records 35 app entries, TV Plus activation paths, shared-library consumers,
the ad agent's live Home Media client, and scoped network observations. Use its
updated dependency findings when planning trials. The owner subsequently
authorized the four-group batch; its application and native no-restart fix are
recorded in the catalogue's maintenance section. The review below predates it.

The owner selected further background cleanup after tabling the Google voice
issue. This phase inspected live process counters, vendor units and selected
app-lifecycle fields from the expanded baseline. No services were stopped,
registry flags changed, or restart performed. Quiet Tizen's reference profile
targets a different model and Tizen version; it is not a ready-to-apply Frame
profile.

| Candidate | Verified evidence | Next step |
| --- | --- | --- |
| PISA `adagent-service` | Still running; its vendor unit uses the same negated `pisa-service` marker already installed. Approximately 7.3 MiB RSS. | First narrow stop trial candidate. The marker blocks subsequent unit starts, but does not terminate an existing process. App-level preloading is also recorded, so verify actual process absence and later restart persistence. |
| Bixby capsule viewer / Alexa client | Resident at approximately 72 / 13 MiB RSS; both have app-level preload enabled in the baseline registry. | Inspect and test app-specific graceful closure before changing preload behavior. Disabling voice preferences did not prove these processes had exited. |
| TV Plus provider | `slive-provider-daemon` is active and has a vendor stop-marker condition, but also DBus/path activation. A TV Plus process is resident. | Establish caller/dependency scope before a separate trial; do not infer isolation from the unit's empty RequiredBy field. |

A 3.33-second sample used the TV's measured CLK_TCK of 250 and 4 KiB pages.
Bixby used approximately 1.9% of one core, Alexa 1.2%, and the ad agent 0.1%.
This short, instrumented sample does not establish a sustained bottleneck or
predict navigation improvement. RSS includes shared pages and is not equivalent
to reclaimable memory. One unrelated process exited during `/proc` enumeration;
the selected candidates were present in both samples. SDK diagnostic staging
and the owned host SDB listener were confirmed cleaned up. Root unit inspection
also completed its cleanup checks.

The selected Bixby, Alexa, ad-agent, TV Plus provider and voice registry entries
all report `app_support_disable=false`. The targeted-ad service additionally
records on-boot, autorestart and preload enabled. These are lifecycle clues,
not permission to overwrite the package registry. Voice units also demonstrate
why unit state alone is insufficient: `org.tizen.voice-app.service` is a
oneshot launcher with MainPID 0 while its app process remains resident.

Keep SmartThings, context services, screen analysis, KPI services, Art Mode and
shared media/network services outside the next narrow trial. The separate
contents-recognition service lacks the verified PISA/canalysis marker mechanism;
do not invent a marker or apply broad runtime masks. Any new trial needs its own
before-state and rollback records. In particular, the existing PISA marker is
shared: removing it to restart only the ad agent would also permit PISA to start.
The earlier PISA rollback remains intact, and the expanded baseline was not
modified. Private review evidence is retained on the Mini under
`~/.local/share/samsung-tv-tools/changes/20261024-background-review/`.
