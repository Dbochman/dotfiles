---
name: samsung-tv
description: Control the Crosstown Samsung Frame TV, resolve and play YouTube videos, Netflix, Apple TV, HBO Max and Peacock content, Spotify, and Twitch channels in their native apps, and operate remote keys, volume, inputs, power, and Art Mode.
allowed-tools: Bash(samsung-tv:*), Bash(samsung-tv-dev:*), Bash(samsung-tv-spotify:*), Bash(python3:*), Bash(websearch:*)
metadata: {"openclaw":{"emoji":"T","requires":{"bins":["samsung-tv","samsung-tv-dev"]}}}
---

# Crosstown Samsung Frame

Use `samsung-tv-dev` for installed-app discovery, app launches, content links,
browser navigation, focused-app inspection, and LAN wake. It runs through SSH on the **Crosstown MacBook,
`dylans-macbook-pro`**, whose TV-facing address is `192.168.165.109`.
The TV is `192.168.165.2`, model QN65LS03BAFXZA, firmware 1720.7 / Tizen 6.5.
`dylans-mac` is a different Mac, not this developer host.

Use the existing `samsung-tv` CLI for remote buttons, volume, power and
Art Mode. Those WebSocket operations originate on the Mini over the existing
Tailscale subnet route; they do not require a developer-shell session.

## Choose the operation

- **Find content by title:** read [title lookup](references/lookup.md). Search for
  candidate provider links, then use `scripts/content_lookup.py` to check YouTube
  title/channel metadata or resolve Netflix's actual season/episode IDs. The
  lookup is read-only; send the resolved link to the playback helper only for a
  play request. Clarify meaningful ambiguity instead of selecting the first hit.
- **Open an app:** discover its exact installed ID, then launch it. App IDs from
  Samsung's store, Tizen and the native player can differ. Do not guess IDs or
  fuzzy-select the first ambiguous match.
- **Play a YouTube video:** use `youtube` below. It targets the YouTube TV app,
  not the Internet browser. Accept the user's link/ID, or find the intended
  public video with available search tools. Clarify ambiguous titles.
- **Play content in another app:** read [content routing](references/content.md).
  For Prime Video, Apple TV, HBO Max, Peacock, Spotify, Crunchyroll or Twitch,
  consult the [installed-app survey](references/apps.md) for real app IDs,
  lookup options and the distinction between inspected and verified routes.
  Discover its link handlers and submit an appropriate link to that explicit app.
  App launch does not establish content selection, autoplay, or subscription access.
- **Play Netflix content:** use `netflix` with an exact content ID or Netflix
  watch/title link. It gracefully restarts Netflix to deliver the request;
  this briefly interrupts current Netflix playback. The normal SDK account
  suffices. Read [content routing](references/content.md) for episode resolution
  and verification. Use ordinary `launch` when only opening the app is wanted.
- **Open/play Apple TV content:** use `apple-tv URL` for details or
  `apple-tv-play URL` for playback. Resolve the exact movie/show/episode link;
  these native requests work without restarting the app. Read
  [Apple TV routing](references/apps.md#apple-tv) for lookup and verification.
- **Open/play HBO Max content:** use `max-show ID_OR_URL`, `max-play ID_OR_URL`,
  or `max-search TITLE`. Resolve the exact video for an episode request. These
  commands gracefully restart HBO Max; see [Max routing](references/apps.md#hbo-max).
- **Open/play Peacock content:** use `peacock-episode ID_OR_URL` for episode
  details or `peacock-play ID_OR_URL` for that episode's playback; `peacock-show`
  opens a series and `peacock-search` accepts title text. Commands gracefully
  restart Peacock. Read [Peacock routing](references/apps.md#peacock) for ID
  resolution and the current verification limits.
- **Open Prime Video content:** use `prime-video GTI` with the exact provider
  episode/movie GTI. Read [Prime routing](references/apps.md#prime-video).
  This opens content; inspect the page before choosing its Play button.
- **Play Spotify on the TV:** use `samsung-tv-spotify play URI`, `pause`,
  `resume`, or `status [TRACK_URI]`. Read [Spotify control](references/apps.md#spotify)
  for title lookup and the isolated client. Do not use Homebrew `spogo` for TV
  playback; it does not reliably honor the selected device.
- **Open a Twitch channel:** use `twitch` with its channel login or exact HTTPS
  channel link. It gracefully restarts Twitch to consume the link, briefly
  interrupting current Twitch playback. Live channels can autoplay; offline
  channels show their page. Use ordinary `launch` just to open the app.
  Read [app validation](references/apps.md) for evidence and limitations.
  VOD links are not yet supported by this command.
- **Open a web page:** use `open` only when a browser is wanted. Never silently
  substitute browser playback for an app request.
- **Pause, volume, input, Art Mode:** use the remote operations below.

A user's request to open/play/control content authorizes that ordinary action;
perform it without an extra confirmation. Stop for login, purchases, ambiguous
content selection, or a new on-screen authorization that needs the owner.

## Before opening or playing content

1. Run `samsung-tv power frame on` and require `power_on_verified: true`
   before launching an app or capturing its UI. Already-on is a passive REST
   check. Confirmed standby uses one remote connection, rechecks power, and
   sends at most one power key if still in standby. Unknown/unreachable uses
   LAN wake from Crosstown first; unknown state never authorizes a blind toggle.
   Failure means stop and report the specific obstacle, not "Netflix is open."
2. Launch the requested app, then capture and inspect its current UI. An app
   launch acknowledgment or focused PID can exist while the panel is off.
3. For a requested profile, inspect the chooser or account UI and select that
   exact profile before submitting content. Do not assume the previous profile,
   infer it from playback, or blindly press Enter. If the home screen is visible,
   use observed navigation to its profile switcher, then verify the selection.

Title lookup can run independently, but power-on, app launch, capture, profile
selection and playback must follow their dependencies in that order.

## App and content commands

```sh
samsung-tv status                         # REST power/reachability; no WS wake
samsung-tv-dev status                     # Developer connection/capabilities
samsung-tv-dev foreground                 # Focused app; not pixels, menus, or playback
samsung-tv-dev screenshot --output /tmp/frame-UNIQUE.png # New private local PNG
samsung-tv-dev apps YouTube                # Filter installed names/IDs
samsung-tv-dev apps                        # Full installed catalog when needed
samsung-tv-dev app-info 9Ur5IzDKqV.TizenYouTube
samsung-tv-dev launch 9Ur5IzDKqV.TizenYouTube
samsung-tv-dev app-state com.samsung.tv.cobalt-yt
samsung-tv-dev youtube 'https://www.youtube.com/watch?v=VIDEO_ID'
samsung-tv-dev youtube-state VIDEO_ID      # Read player-reported state; not screen proof
samsung-tv-dev netflix 'https://www.netflix.com/watch/80094030' # Example: Our Planet S1E3
samsung-tv-dev twitch 'https://www.twitch.tv/bobross'
samsung-tv-dev apple-tv 'https://tv.apple.com/episode/umc.cmc.23qox5n6larygqgg6u74fvyj'
samsung-tv-dev apple-tv-play 'https://tv.apple.com/episode/umc.cmc.23qox5n6larygqgg6u74fvyj'
samsung-tv-dev max-search 'The Last of Us'
samsung-tv-dev max-show '93ba22b1-833e-47ba-ae94-8ee7b9eefa9a'
samsung-tv-dev max-play 'https://play.hbomax.com/video/watch/dae9e532-3714-4f2e-b758-fb9a13def902'
samsung-tv-dev peacock-search 'The Office'
samsung-tv-dev peacock-episode '58fabbf4-9291-3575-bcdc-bd25eee192c8'
samsung-tv-dev peacock-play '58fabbf4-9291-3575-bcdc-bd25eee192c8'
samsung-tv-dev browser                    # Launch Internet, retain current page
samsung-tv-dev open 'https://example.com/' # Navigate Internet
samsung-tv-dev launch APP_ID --url 'CONTENT_URI'
```

Replace placeholders; always quote links. `youtube` accepts ordinary watch,
short, shorts, live and embed links or an 11-character ID; `t`/`start` offsets
are supported. It uses local DIAL discovery and YouTube's playback-control API,
keeps session values in memory, and has no browser fallback. The generic
`launch --url` path is useful for apps that handle Tizen `view` URIs; it did
**not** select the video in our YouTube test. Use the dedicated command there.

`netflix` accepts numeric content IDs or exact `netflix.com` / `www.netflix.com`
HTTPS watch/title links. Query parameters are not forwarded. Use an episode's
watch ID for an exact episode; a series title ID leaves episode choice to Netflix.
Netflix controls saved progress, profile selection and entitlement. Do not call
`netflix` to check status: each call can restart the app and change playback.
An accepted request remains separate from observed playback.

## Remote, power and Art Mode

```sh
samsung-tv-dev wake                       # LAN WoL from Crosstown Mac
samsung-tv power frame on                 # Verified power-on; safe already-on check
samsung-tv power frame off
samsung-tv key frame KEY_PLAY
samsung-tv key frame KEY_PAUSE
samsung-tv key frame KEY_STOP
samsung-tv key frame KEY_RETURN
samsung-tv key frame KEY_HOME
samsung-tv key frame KEY_ENTER
samsung-tv key frame KEY_UP               # Also DOWN / LEFT / RIGHT
samsung-tv keys frame LEFT ENTER          # Short observed batch, one WS connection
samsung-tv key frame KEY_HDMI1
samsung-tv volume frame up                # Or down / signed number of presses
samsung-tv mute frame                     # Toggle, not an absolute mute setting
samsung-tv art frame                      # Read Art Mode (opens WS)
samsung-tv art frame on                   # Or off
samsung-tv art frame list
samsung-tv art frame select ART_ID
```

Volume numbers count key presses; they are not an absolute volume level.
`keys` accepts 1–16 navigation/transport keys, closes its connection, and never
retries a partially sent batch. Inspect the screen before resuming after failure.
Avoid unbounded or blind key sequences. `mute` is a toggle: do not claim a known
muted state when the starting state is unknown. `wake` sends a packet only;
use `power frame on` for ordinary wake requests and to recover from unsuccessful
WoL. Do not stop at failed WoL and ask the owner to use a remote when the verified
remote fallback remains available. Do not send WoL from the Mini and assume it
crossed Tailscale.
A WebSocket connection can light the panel/show a notification; use REST status
for passive checks. Stop after a connection timeout or prompt rather than repeatedly
triggering approval dialogs or toggles.

For an owner-requested **full TV restart**, use the maintainer procedure in
`/Users/dbochman/dotfiles/docs/samsung-tv-developer-control.md`, section
"Full remote restart (maintainer operation)". A full remote restart was verified
through Tizen's vendor API; ordinary off/on is not reboot proof, and there is no
`samsung-tv-dev reboot` command. The procedure gates dispatch on completed
temporary-root cleanup and verifies a changed boot ID. Do not substitute a
forced reboot or run the raw reboot request inside a live root probe.

Current privacy guards and remaining checks are summarized in
`/Users/dbochman/dotfiles/docs/samsung-tv-privacy-catalogue.md`. Four additional
AOV/far-field masks are temporary to each boot; the app flags and vendor stop
markers are persistent and survived the verified restart. An inactive voice
service or an AOV gate's exit 123 is not a reason to re-enable it for ordinary
playback or cloud remote control.

## Verification and recovery

The helper distinguishes `launch_acknowledged`, `uri_submitted`,
`playback_request_accepted`, and `playback_verified`. A running process is not
proof of foreground display or playback. Confirm actual playback through reliable
player state when available, or ask what the owner sees. Otherwise report the
request as sent/accepted, not “now playing.” Avoid resending a successful playback
request just to check status; that restarts the video.

Use `foreground` after an app launch to identify the focused window's application.
It checks focus before and after PID-to-app mapping; unstable or unmapped focus
does not verify an app. `foreground_verified` confirms that mapping only, not
the current page, selected profile, highlighted control, panel power, or playback.
Do not claim to see Netflix's home screen or chooser from a focused Netflix PID.

For visual navigation, use `screenshot --output NEW_LOCAL_PNG`, then inspect that
file with the available image-viewing tool. The owner authorized temporary root
for this purpose. The helper uses the reviewed archive-launcher route, captures
the focused application's surface, transfers only that image, and removes its
TV staging. It checks root identity, focus stability, source hashes and cleanup.
No kernel-memory exploit, UEP change, or persistent root daemon is involved.
Netflix screenshots were visually verified on 2026-10-24, including readable
menus and selection highlighting. Prefer capture → a small number of purposeful
remote keys → fresh capture. Do not treat the image as a live stream, a complete
composite of system overlays, or proof that protected video is playing. Store
screenshots outside Git and avoid capturing login/payment details unnecessarily.

If capture fails, read its actual error and check REST power. A standby/no-focus
failure does not establish that screenshots are unavailable. When the helper
confirms cleanup, verify power-on, allow the UI to settle, then retry capture
once with a new output path. If cleanup is unconfirmed, stop developer operations
until a maintainer verifies the exact reported staging paths; do not bypass that
guard. Stop on ownership/hash/root verification errors. After a bounded retry
fails, describe that failure and ask what the owner sees; do not ask them to
identify a profile before attempting this supported recovery.

After a YouTube play request, use `youtube-state VIDEO_ID` for supporting evidence
without starting a stopped app or changing playback. A matching video and playing
state produce `requested_video_playing_in_api: true`, but **not** verified visible
playback: this TV reported the requested video and advancing time while the owner
still saw its account chooser. `playback_verified` therefore remains false.
Treat screen observation as authoritative when it conflicts with the API.

If an account chooser is visible, use the owner's requested profile. Inspect a
fresh screenshot for the highlighted tile and send only the necessary keys.
For **Netflix**, the owner confirmed on 2026-10-24 that **Julia's profile is blue
and at the top of the profile list**. Use this as a navigation hint; confirm the
Julia label and current highlight before selecting, since layout/order can change.
This chooser shows the name beside the highlighted profile only. If Dylan's
yellow tile is highlighted directly below Julia's blue tile, move Up once and
capture to confirm Julia; do not explore unrelated profiles to find her name.
If capture still fails after the recovery above or the highlight is ambiguous,
ask the owner. Dylan's profile was selected
with Enter when highlighted on 2026-10-24; do not assume it will always be highlighted.
Confirm the chooser closed. If the owner sees the home screen, one fresh request
for the already-requested video is appropriate after that state change. Do not
infer successful profile selection from API playback state.

Netflix-specific recovery and resume behavior from the successful follow-up:

- A full-screen promotional screensaver can hide the chooser. When a fresh
  capture identifies that state, one `KEY_RETURN` dismissed it in the verified
  run. Capture again; do not use Return indiscriminately during playback.
- After Enter on Julia, the chooser closed and Netflix home showed her blue
  avatar at top left. That observed transition confirms selection; highlighting
  a profile alone does not.
- For a general "put on SHOW under PROFILE" request, select the profile first
  and use the resolved series ID to let Netflix choose its saved/preferred
  episode. Do not substitute episode one or the latest episode. An explicit
  episode request still needs its exact watch ID. See [Netflix routing](references/content.md#profile-selection-and-series-resume).
- Once the owner confirms playback, stop verification and leave it running.
  A black video layer can coexist with readable episode/subtitle overlays;
  report those as supporting evidence, not independent proof of moving video.

If developer access fails, check the Crosstown Mac's SSH reachability and that
its LAN address still matches the TV's Developer IP. If an SDB server already
exists, leave it alone. The helper owns only its temporary server, listener,
and staging files; it reports uncertain cleanup as a failure. Do not replace
it with arbitrary SDK shell injection, install a root controller, alter Home
routing, or disable TV security settings to complete ordinary controls.

The browser and stock curl accepted compromised eMudhra test chains on
2026-10-24. Avoid sensitive browser logins until remediated; this finding does
not imply every streaming app uses the same trust path. For maintainer diagnostics
and evidence, see `/Users/dbochman/dotfiles/docs/samsung-tv-developer-control.md`.
