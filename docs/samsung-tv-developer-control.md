# Crosstown Samsung TV developer control

Verified 2026-10-24 on QN65LS03BAFXZA (2022 Frame), firmware
T-PTMAKUC-1720.7, Tizen 6.5 / ARMv7. Developer Mode is enabled for the
Crosstown MacBook's LAN address, 192.168.165.109. The TV is 192.168.165.2.
Use SSH host `dylans-macbook-pro`; `dylans-mac` is a different machine.

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

The developer/content helper runs on the Crosstown Mac over SSH; standard
WebSocket keys, volume and Art Mode remain on the existing Mini-side `samsung-tv`
CLI. The skill explains this split and keeps wake broadcasts on the Crosstown LAN.
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
