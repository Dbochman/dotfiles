# Content routing

Use this reference when selecting a particular film, episode, stream, playlist,
or video rather than merely opening an app.

When the request names content rather than supplying a link/ID, first use
[title lookup](lookup.md) to discover and check the provider's identifier.

## YouTube

Use `samsung-tv-dev youtube VIDEO_URL_OR_ID`. The public launcher on this TV is
`9Ur5IzDKqV.TizenYouTube`; the native player is `com.samsung.tv.cobalt-yt`.
The dedicated command discovers the local YouTube screen through DIAL, obtains
an ephemeral control token, and submits one `setPlaylist` request. It replaces
the current queue with the requested video. Do not use it when the user merely
asks to add to a queue; queueing is not implemented.

Then run `samsung-tv-dev youtube-state VIDEO_ID`. The attended test's requested
video `eyZEqSPeMwg` was subsequently reported by the native player's playback
channel with state `1` and advancing time (419.883 → 420.349 seconds), **while
the owner still saw the account-selection screen**. This is API evidence only,
not visible-playback proof. The read-only command reports
`requested_video_playing_in_api` separately and keeps `playback_verified` false.
It does not relaunch the app, resend the playlist or restart the video. The owner
subsequently identified Dylan as highlighted; Enter selected that profile and
the owner confirmed YouTube's home screen appeared.
After one fresh request for the same video, the owner confirmed visible playback.
This is the verified profile-selection → playback sequence; the API alone did
not establish the first attempt's result.

Both a generic Tizen URL launch and a DIAL launch with `v=VIDEO_ID` opened
YouTube without selecting the video in the attended test. They are not fallback
playback methods. YouTube's private playback-control API can change; on failure,
stop, report the stage, and preserve the app's current state. Do not print the
DIAL additionalData block, screen IDs, session IDs, tokens, or raw API bodies.
The helper returns only operational status and the requested public video ID.

Protocol reference: [ytcast's YouTube implementation](https://github.com/MarcoLucidi01/ytcast/blob/master/youtube/remote.go).
The helper implements the required protocol using Python's standard library;
it does not install or run the upstream binary.

## Netflix

### Direct native playback

Use `samsung-tv-dev netflix CONTENT_URL_OR_ID`. On this LS03B/1720.7 build,
Netflix's running-app content dispatcher is an empty native stub, so sending a
new deep link to the running process silently fails. The helper instead checks
whether Netflix is running, gracefully terminates it if needed, confirms it has
stopped, and launches it once with the requested ID in its startup parameters.
It uses the normal SDK account and needs neither root nor an application patch.
There is no browser fallback or keyboard search.

The owner confirmed two exact episodes playing on 2026-10-24:

- *Our Planet* S1E2 **Frozen Worlds**, ID `80094031`, through the diagnostic
  cold-launch path.
- S1E3 **Jungles**, ID `80094030`, through the shipped helper using SDK UID 901.

```sh
samsung-tv-dev netflix 'https://www.netflix.com/watch/80094030'
samsung-tv-dev netflix 80094031
```

A play request authorizes the required Netflix restart. It interrupts current
Netflix playback; use ordinary `launch org.tizen.netflix-app` to open the app
without requesting content. The helper does not force-kill a process, alter
account data, or silently retry a partially completed request. If stopping fails,
it does not submit a new launch. On a launch failure, inspect the current state
before retrying.

Use an exact episode's watch ID rather than the series ID when the user asks for
an episode. Netflix's public metadata explicitly maps season/episode numbers to
IDs. For example, *Our Planet* series ID is `80049832`, S1E1 is `80094026`, S1E2 is
`80094031`, and S1E3 is `80094030`. Sorting IDs gives the wrong episode order.
Resolve a supplied title against authoritative Netflix metadata; clarify ambiguous
content rather than inventing an ID. The helper does not accept a free-text title.

The helper returns `content_request_submitted` and `launch_acknowledged`, keeping
`playback_verified` false. Confirm actual playback from a screen capture with
identifying player controls or the owner. A blank protected-video capture is
inconclusive. Do not restart Netflix again just to check status. A fresh login,
profile chooser, maturity gate or unavailable title may still need attention.
The command does not force a profile, playback offset or restart-from-beginning.
A series ID can select Netflix's preferred episode instead of a particular one.

### Earlier tests and app/profile control

Attended tests on 2026-10-24, QN65LS03BAFXZA / firmware 1720.7:

- Installed application: `org.tizen.netflix-app`.
- `samsung-tv-dev launch org.tizen.netflix-app` opened Netflix's profile chooser.
- The owner identified Dylan as highlighted. One `samsung-tv key frame KEY_ENTER`
  selected that profile; the owner confirmed Netflix's home screen appeared.
  Check the current highlight before repeating this on another occasion.
- `app-info` advertised DIAL support (`.netflix.com`) but no standard Tizen
  app-control URI handlers. This is not proof that all content routes are impossible.
- Local DIAL `/ws/apps/Netflix` reported version `70.23.15090` and `running`
  after launch. This does not establish foreground display or playback.
- Using Netflix's official *Our Planet* title ID `80049832`, DIAL POSTs with
  both JSON `{"v":"80049832"}` and form `v=80049832` returned HTTP 200,
  but the owner observed no content change. Neither format is a verified title
  or playback route on this TV; do not retry these as an automatic fallback.
- In an owner-requested retest after the TV was off, LAN wake was sent from the
  Crosstown Mac, REST reported power on, and the owner confirmed Netflix home
  after SDB launch. A WebSocket `DEEP_LINK` request to the installed Tizen ID
  with metaTag `80049832` also left Netflix on its home screen. This exact
  request is unverified for title selection; no other ID/payload variants were
  established by that test.

### Observed UI fallback

The owner confirmed *Our Planet* was playing. Focused-app captures independently
identified **S1:E1 “One Planet”**, first at 03:49 and later at 13:21, 15:15 and
19:10, with the Pause control shown. The protected video plane was black in the
capture; subtitles, title, episode and player controls remained readable. The
owner's screen observation establishes visible video playback.

The successful route was Search → `our` → *Our Planet II* → Episodes → S1E1 →
Enter. The result tile used the latest season's title but opened the series with
both seasons. In the observed six-column keyboard, rows were `abcdef`, `ghijkl`,
`mnopqr`, `stuvwx`, `yz1234`, `567890`. Derive short key batches from the actual
highlight; do not replay the whole sequence from an unknown starting screen.
From Episodes, Down entered the episode carousel. Left from S2E1 crossed into
S1E8; seven further Left presses reached S1E1. Inspect the selected episode's
label before Enter. Layout, profile and resume position may change.

Use `samsung-tv keys frame KEY...` for a short sequence through one connection
(maximum 16 navigation/transport keys; no automatic retry after partial failure).
This reduces connection overhead; screenshots still establish resulting state.
UP exposed Netflix's player controls in the tested playback state. Temporary
root setup can outlast the controls' visibility, so a capture without controls
is inconclusive. The successful diagnostic revealed controls immediately after
root became ready; that timing hook is not yet a public helper option. Discrete
PLAY/PAUSE key behavior has not been independently established.

### Direct routing investigation

After each of these additional requests, S1E1 was still playing when checked
on screen; none is a verified episode shortcut:

- DIAL body `m=https://www.netflix.com/watch/80094031&source_type=4`.
- Native `eden_resume` with PAYLOAD `{"values":"m=https://www.netflix.com/watch/80094031&source_type=4"}`.
- Native `action_play_url=movieid=80094031`, with `NETFLIX_SOURCE_TYPE=4`.
- Native `eden_resume` with `action_play_url=m=https://www.netflix.com/watch/80094031&source_type=4`
  and `NETFLIX_SOURCE_TYPE=4`.

The later successful cold launch supersedes the earlier conclusion that direct
playback was unavailable. The exact working payload is
`action_play_url=m=ID&trackId=15539201&showMovieInfoOnly=false` plus
`NETFLIX_SOURCE_TYPE=4`, delivered to a fresh Netflix process. Do not use those
fields against an already-running app on this build. The helper implements the
required stop/wait/launch sequence.

The distinction was verified by reading only launch-state fields and the dynamic
binding of `sendSystemCommand`: the warm request reached the native launch
handler, but its final dispatch function was a no-op. No Netflix binary was
patched. The runbook records the evidence and diagnostic cleanup.

Payload reference: [Samsung Smart Hub fixtures in Yandex RTV](https://github.com/yandex/rtv/blob/c387c23aa6b3826993ee930a0dac0950dfbf2bd7/packages/server/test/mocks/tizen24/eden-apps.json).
That older capture established numeric `m` and the content-page fields; live tests
on this TV established the required cold launch and actual episode playback.

Test title: [Our Planet on Netflix](https://www.netflix.com/title/80049832).
The initial DIAL payload candidate came from
[Connect SDK's DIALService](https://github.com/ConnectSDK/Connect-SDK-Android-Core/blob/master/src/com/connectsdk/service/DIALService.java);
the local screen observations take precedence over that older implementation.

## Other apps

1. Run `apps NAME`; resolve the intended installed app explicitly.
2. Use `app-info APP_ID` to read advertised app-control operations, URI schemes,
   and selected link metadata. A declared handler is a candidate, not a verified
   content route. Empty controls mean unknown support, not necessarily impossible.
3. Resolve the user's intended content using an authoritative service link or
   known content identifier. Do not invent Netflix/Prime/Disney content IDs or
   assume that a link for another region/account is playable.
4. If the app accepts Tizen `view` links, submit
   `samsung-tv-dev launch APP_ID --url 'CONTENT_URI'`. This selects that app;
   it does not delegate the link to a browser. For other operations or proprietary
   payloads, research the app's current interface before extending the helper.
5. Verify the requested content and playback. A detail page, login screen, ad,
   age gate, or purchase prompt is not verified playback. Report the actual state.

If a content route is unsupported, opening the requested app is useful partial
progress, but say that content selection remains unresolved. Use observed UI
navigation where available; do not issue long blind remote sequences. Offer
browser playback only as an explicit alternative when appropriate.

## Capability boundaries

- Verified developer path: installed-app discovery and browser navigation,
  with a JavaScript callback and owner-visible confirmation.
- Verified YouTube app launch: owner observed the native app open.
- `foreground` reads focused PID and maps it to an application, checking focus
  again before accepting the mapping. Verified live with Netflix. This provides
  app-level focus evidence, not UI content or playback observation.
- YouTube's player-reported video/state are readable, but cannot establish
  profile selection, foreground UI, or visible playback. Request acceptance,
  API state and screen observation are separate evidence.
- Other apps' deep links, remote-key reactions, Art Mode transitions, and
  wake-from-off require their own behavioral verification; app metadata alone
  does not establish them.
- `screenshot --output NEW_LOCAL_PNG` now captures the focused app's surface
  through a temporary, owner-authorized root session. Netflix's menus, text and
  highlighted Play button were visually verified. Inspect the actual PNG before
  describing the UI; a successful command alone is not visual verification.
- There is no browser DOM inspector, semantic UI tree, or universal streaming
  playback-status API in this helper. A focused-app capture is not necessarily
  the complete panel composite: other system overlays or protected video may
  be missing. Do not infer playback from a promotional image or still frame.
