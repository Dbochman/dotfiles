# Additional TV apps: inspected routes

Surveyed 2026-10-24 on the Crosstown Frame, firmware 1720.7 / Tizen 6.5.
All seven apps below are installed. The initial read-only survey inspected
manifests, static launch resources, public provider pages and Spotify's
existing integration. Subsequent owner-authorized live tests are recorded below;
an acknowledged launch alone is not proof of content selection or playback.
Package versions describe the installed wrapper, not necessarily the currently
served web app. Recheck IDs after a reinstall.

## Installed targets

| App | Real application ID | Package version | Best next control route |
| --- | --- | --- | --- |
| Prime Video | `org.tizen.primevideo` | 5.3.22 | Verified native GTI detail route |
| Apple TV | `com.samsung.tv.aria-video` | 16.2.0 | Verified native URL details/play |
| HBO Max | `5b8c3eb16b.BeamCTVDev` | 6.0.0 | Verified fresh-start, intact-string payload |
| Peacock | `gzcc4LRFBF.Peacock` | 7.7.203 | Verified search/episode details; native play submitted |
| Spotify | `rJeHak5zRg.Spotify` | 2.1.71 | Verified Frame-only Connect helper |
| Crunchyroll | `OGLLvqej7u.CrunchyrollWebApp` | 15.6.12 | Inspect Smart Hub resume payload |
| Twitch | `Jz7aUrV2Q4.Twitch` | 12.0.2 | Samsung `PAYLOAD` containing a full Twitch URL |

Use `samsung-tv-dev launch APP_ID` for an ordinary open-app request and verify
foreground/screen as usual. IDs are inventory-confirmed; live launch results
are listed separately below. Do not choose the placeholder
`org.tizen.primevideo-dummy`, `com.samsung.tv.aria-dummy`, or background services.
Twitch live-channel playback is owner-confirmed. Other new direct-content
routes remain unverified unless an explicit result below says otherwise.
`launch APP_ID --url URL` sends a standard Tizen view URI; it does **not** send
the vendor `PAYLOAD` described here.

## Live launch results

- **Prime Video:** Fallout S2E1 selected directly; owner confirmed playback after selecting its visible Play button.
- **Apple TV:** Severance S1E2 **Half Loop** opened directly; playback confirmed by owner.
- **Peacock:** The Office search and S1E1 Pilot selection visually verified; native play submitted, owner playback confirmation pending.
- **Crunchyroll:** native app focused; “Session expired” / `WCP-UN-0` login screen.
  Stop playback testing until the owner completes fresh login; do not retry credentials.
- **HBO Max:** The Last of Us show selected directly; direct video playback confirmed by owner.
- **Spotify:** native playback of Queen’s Bohemian Rhapsody confirmed by owner;
  exact Frame/track verified through Connect, with targeted resume and pause tested.
- **Twitch:** BobRoss direct-channel playback confirmed by owner.

Profile choices, subscriptions and purchases are separate from an app launch.
No profile was created or account setting changed by this validation.

## HBO Max

Use the typed native commands:

```sh
samsung-tv-dev max-show '93ba22b1-833e-47ba-ae94-8ee7b9eefa9a'
samsung-tv-dev max-play 'https://play.hbomax.com/video/watch/dae9e532-3714-4f2e-b758-fb9a13def902'
samsung-tv-dev max-search 'The Last of Us'
```

`max-show` opens a show; `max-play` takes a video UUID or exact provider
`/video/watch/UUID` link; `max-search` opens title search. A show's suggested
Watch button can reflect saved progress and is not an exact episode request.
Resolve a specific episode's video ID separately. Search text containing comma
or question mark is rejected because those delimit the provider payload.

The helper checks installation, gracefully stops a running HBO Max app, waits
for a recognized stopped state, then sends one `aul_test launch` request with
`PAYLOAD` equal to `id=IDENTIFIER,type=show|video|search`. This briefly interrupts
existing playback. It uses SDK UID 901, without root, a browser fallback, an
inspector or app modifications. It requires the launcher's positive-PID success
record: `aul_test` returns that PID as its shell exit status, so a nonzero exit
alone is not a failed request. Failure/unknown stop never sends another launch.

On 2026-10-24, The Last of Us show page was visually verified after a native
fresh start. Its video link above then played The Last of Us, confirmed by the
owner. The captured protected video surface was black with subtitles; the owner
confirmation establishes playback. Exact episode identity for that catalog link
was not independently confirmed, so do not label it S1E1 from a show offer alone.

The earlier failures had two causes, established through the app's temporary
per-app inspector: `/usr/bin/launch_app` splits commas into array elements, but
HBO Max reads only `PAYLOAD.value[0]`. Warm native launches also updated the
requested control without dispatching the app's `appcontrol` event. A proper
Tizen ApplicationControl delivered one intact string and successfully navigated
search and show routes. `aul_test` preserves the string; a fresh process consumes
it on startup. No patched launcher is needed. The diagnostic inspector was
closed and its port verified unavailable before ordinary playback validation.

The current served and running app both reference the
[Tizen adapter](https://tizen.play.max.com/tizen.e9428a2614b82476.sha.js),
SHA256 `1f18d8c6924785b3ea824c07384d5d3801728835bd154f7a7dec36779dc2968d`.
Its downstream router supports `show`, `video` and `search`; these typed commands
avoid exposing arbitrary route/voice payloads. Acknowledgment still does not
prove entitlement, profile selection or playback for each new request.

## Twitch

The manifest has no advertised app-control handler, but the installed config's
hosted TV app contains a Samsung-specific parser. It finds `PAYLOAD`, constructs
a URL from its value, and passes that URL's pathname to the app router. The
Samsung wrapper reads `getRequestedAppControl().appControl.data`.

The normal SDK account successfully requested BobRoss with:

```sh
/usr/bin/launch_app Jz7aUrV2Q4.Twitch PAYLOAD https://www.twitch.tv/bobross
```

The supported agent command is `samsung-tv-dev twitch bobross` (or the full
HTTPS channel link). It checks installation and running state, gracefully
stops Twitch if necessary, waits for a confirmed stop, then sends one request.
A failed/unknown stop never falls through to another launch or force-kill.
No explicit operation key was needed. The focused Twitch surface showed
BobRoss's live “A Happy Little Weekend Marathon!” and a Pause control; the
owner confirmed the video was actually playing. The protected video layer is
black in the capture, so that visual evidence alone was not called playback
proof. No browser, login, root launch or keyboard search was needed. VOD
playback remains untested. A second request selected Monstercat's actual
channel page; the app explicitly showed **OFFLINE**. No attempt was made to
follow the channel or substitute a VOD. This confirms a second channel route,
not live playback for an offline broadcaster. A subsequent BobRoss request
while Twitch was already foreground was acknowledged but left exactly the
same Monstercat surface and PID. The helper therefore uses a fresh process
for channel requests. The updated helper was then verified to create a new
Twitch process and select BobRoss again; its captured ad screen explicitly
named BobRoss. Ads and offline status are normal provider outcomes, not
reasons to retry. Do not infer that absence of a manifest handler
means absence of an implemented parser.

For discovery, resolve the requested broadcaster to the actual channel login,
or distinguish a VOD from a live channel. Twitch's documented API offers channel
search and live-stream checks with authorization; public page discovery can
identify a link without setting up a new API integration. Do not equate a valid
channel URL with a live stream. The documented `twitch://` links are for mobile,
not evidence of Samsung TV support.

Source: installed-config entrypoint
`https://pewter-ewmmfpytzbnznrg12.tv.twitch.tv/app-shell`, whose
[`_app` bundle](https://pewter-ewmmfpytzbnznrg12.tv.twitch.tv/_next/static/chunks/pages/_app-ac5b3b924e0e6566.js)
has SHA256 `6aae431e637240cee7706020faf5bf3faad59a161d3d5b9f8d220a6dccd42ae7`.
References: [Twitch API](https://dev.twitch.tv/docs/api/reference),
[mobile deep links](https://dev.twitch.tv/docs/mobile-deeplinks/).

## Spotify

The existing `spotify-speakers` skill and `spogo` CLI can search and read
metadata. Opening Spotify made **Samsung The Frame 65** discoverable as an
unrestricted Connect device. The app's home screen and signed-in state were
verified visually.

Use the Mini's dedicated helper:

```sh
samsung-tv-spotify play 'spotify:track:2JiDi0qAXsPwhPqA2qaKGt'
samsung-tv-spotify status 'spotify:track:2JiDi0qAXsPwhPqA2qaKGt'
samsung-tv-spotify pause
samsung-tv-spotify resume
```

`play` accepts Spotify track, album and playlist URIs or exact HTTPS provider
links. Album/playlist playback is supported by the upstream client but has not
been separately tested on the TV. Resolve names with read-only `spogo search`
and check artist/edition metadata before playback. The helper opens the native
TV app for play/resume, discovers a unique Frame TV, and sends its exact device
ID through the Connect engine. Missing/ambiguous/restricted targets fail. It
never transfers to an arbitrary active device or uses a browser/local-player
fallback. Status polls are read-only; delayed state never repeats playback.

On 2026-10-24, Queen's **Bohemian Rhapsody**, album **A Night At The Opera**,
was shown in the native TV UI and reported playing on the Frame. The owner
confirmed audio from the TV. Resume and pause through the final helper were
verified against the actual Frame/track. Each new request still reports API
state separately from audible verification.

Homebrew `spogo` 0.1.0 remains unchanged. Its Connect play path ignores the
requested `--device`; 0.10.3 only resolves that selector when no active device
exists. The isolated `0.10.3-frame.1` build patches explicit targeting for play,
pause and other player writes, bypasses cached active-device routes when a
target is supplied, and rejects unavailable/ambiguous/restricted targets.
The upstream Spotify/app tests and added targeting regressions pass.

Rebuild with `python3 scripts/build_spogo_targeted.py` from this skill's directory.
It checks out commit `980cf0c3137518423638c8b0a4ca6d3ea5cf962c`, applies the
tracked patch including regression tests, tests and builds with the local Go
compiler, and installs under `~/.local/share/samsung-tv-tools/spogo-0.10.3-frame.1`.
The helper verifies the build receipt, source/patch identity and binary digest.
The tested compiler was Go 1.27.3. No new credentials, browser import, global
upgrade, profile change or background service was needed.

Sources: [upstream commands](https://github.com/openclaw/spogo/blob/v0.10.3/internal/spotify/connect_commands.go),
[transport](https://github.com/openclaw/spogo/blob/v0.10.3/internal/spotify/connect_transport.go).

The installed config declares these native payload formats; their presence
does not establish working playback:

```json
{"uri":"%%CONTENT_ID%%","origin":"music_id"}
{"uri":"spotify:search:%%ARTIST_NAME%%+%%TRACK_NAME%%","origin":"music_search"}
```

Warm `eden_resume` and fresh-process default-operation `music_id` PAYLOAD
requests were acknowledged but left Spotify on its home screen without the
requested track. The final read-only status was paused and did not identify
the Frame as the active device. These native payloads are not used by the
verified Connect helper.
The official [playback API](https://developer.spotify.com/documentation/web-api/reference/start-a-users-playback)
supports explicit device targeting and requires Premium; `spogo` is the existing
web-session client, not a newly configured OAuth application.

## Apple TV

Use the native helper with a verified provider link:

```sh
samsung-tv-dev apple-tv 'https://tv.apple.com/us/episode/half-loop/umc.cmc.23qox5n6larygqgg6u74fvyj'
samsung-tv-dev apple-tv-play 'https://tv.apple.com/us/episode/half-loop/umc.cmc.23qox5n6larygqgg6u74fvyj'
```

`apple-tv` requests details; `apple-tv-play` explicitly requests playback.
The helper accepts movie/show/episode HTTPS links, retains the type and content
ID, discards user-supplied query parameters and sends native `PAYLOAD` JSON
whose `values` is a **string**. Only the play command adds `?action=play`.
It works with the running app and does not restart it. Routine requests use
SDK UID 901; no root, browser or keyboard search is needed.

The installed Aria manager parses this JSON envelope; Apple's installed
`app.js` handles `Application.onControl`, queues links through profile selection,
and routes provider URLs by entity type and ID. Its SHA256 is
`0af3de8ce4a4b5e33e76f91a90910ec8e835efb73f1164e1647656177ff2e3f3`.
Static source was read without modifying the app or accessing account files.
On 2026-10-24, the Half Loop detail page and season/episode identity were captured;
the play request started a trailer and the owner then confirmed **Half Loop**
playing. The final helper's canonical URL also opened the correct episode page.
Other movies/shows are supported by the inspected router but not separately
playback-tested. Entitlement, trailers, saved progress and profile selection
remain Apple-controlled. Stop at purchase/trial/login screens.

Resolve names using Apple's public pages: the
[Severance series page](https://tv.apple.com/us/show/severance/umc.cmc.1srk2goyh2q2zdxcx605w8vtx)
links to [S1E2 Half Loop](https://tv.apple.com/us/episode/half-loop/umc.cmc.23qox5n6larygqgg6u74fvyj).
Verify season/episode metadata; a show ID does not select a specific episode.
Launch acknowledgment is not a new playback verification.

## Prime Video

Prime's native Aria application is `org.tizen.primevideo`. Its
[Fallout Season 2 page](https://www.primevideo.com/detail/0KBDY8OBBE3Y0RV5FOQH96H3N6)
exposes episode GTIs in public page data. A season ID is not an episode ID;
resolve the requested episode and region/edition before constructing a request.
Use `samsung-tv-dev prime-video GTI`. It sends native `PAYLOAD` JSON with
`values` containing the exact `amzn1.dv.gti.UUID` string, without restarting
Prime or pressing any keys. Public web detail IDs are not accepted as GTIs.
Prime can show its profile chooser again on a later request even with the same
process. Inspect it and select the known requested profile; never blindly send
Enter or assume the profile choice persists across requests.
On 2026-10-24 the public season page mapped **The Innovator (S2E1)** to
`amzn1.dv.gti.22e31bb7-24ed-4ee1-a15c-800a1e9da25f`. This request survived
Dylan's profile selection and showed Fallout with **Play S2 E1**. Selecting
that observed button started ads, then the owner confirmed the episode playing.
The final typed helper was also verified after selecting Dylan: the captured
page showed **Resume S2 E1**, the matching synopsis and **Included with Prime**.
The native request alone selects content; autoplay has not been established.
Capture the resulting page before selecting a visible Play control, and stop
at rental, purchase, subscription or trial offers. Do not infer that every
acknowledged GTI is available under the active profile/region.

## Peacock

Use the public episode UUID, or its exact Peacock episode-page link:

```sh
samsung-tv-dev peacock-search 'The Office'
samsung-tv-dev peacock-show '4902514835143843112'
samsung-tv-dev peacock-episode '58fabbf4-9291-3575-bcdc-bd25eee192c8'
samsung-tv-dev peacock-play 'https://www.peacocktv.com/watch-online/tv/the-office/4902514835143843112/seasons/1/episodes/pilot-episode-1/58fabbf4-9291-3575-bcdc-bd25eee192c8'
```

`peacock-episode` requests details with the exact episode selected;
`peacock-play` requests that episode's playback. `peacock-show` takes a numeric
series ID or exact series-page link and requests details, never an arbitrary
first episode. `peacock-search` submits title text. Movie/live-channel requests
are not exposed by these commands. They use the same bounded graceful restart
and single native `aul_test launch` transport as HBO Max, with SDK UID 901.
Each request can interrupt current Peacock playback; use ordinary `launch`
for an app-only request and do not resend a content request as a status check.

The Samsung adapter parses `PAYLOAD.value[0]` as JSON, takes its string `values`,
and extracts the URL's `deeplinkData` parameter. The helper constructs:

```json
{"type":"EPISODE","action":"PLAY","pvid":"58fabbf4-9291-3575-bcdc-bd25eee192c8"}
```

That object is URL-encoded twice into
`https://www.peacocktv.com/deeplink?deeplinkData=...`, then wrapped as
`{"values":"URL"}`. Details use `PDP`; series use `type=SERIES`,
`providerSeriesId` and `PDP`; search uses `{"search":"TITLE"}`.
The adapter decodes the whole URL before extracting the parameter; the worker
decodes the parameter again. Two encoding layers preserve title punctuation
such as ampersands and percent signs. No arbitrary JSON, watchlist mutation or
account operation is exposed.
An ordinary public watch URL alone is not this protocol.

On 2026-10-24, the actual running app's inspector verified the search route;
root capture showed The Office results. The exact Pilot UUID then opened the
series page with **Pilot, S1 E1** highlighted, verified in a fresh capture.
The internal public-page `contentId` (`GMO_00000000059625_01`) did not navigate;
use the public episode URL's UUID as `pvid`, not that internal ID. The final
helper gracefully restarted Peacock without debugging and submitted the Pilot
play request. Its focused surface was black; owner playback confirmation is
pending. Search and episode details were verified through the equivalent Tizen
ApplicationControl payload; the helper's standalone series command is based on
the inspected parser and has not been separately screen-tested.

The temporary per-app inspector revealed the hosted app at
`https://tv.clients.peacocktv.com/samsung.html`; there was no need to decrypt or
modify the packaged `.spm` wrapper. Both diagnostic inspector ports were closed
and verified unavailable after ordinary native launch. Static source evidence:

- [Samsung adapter/main bundle](https://tv.clients.peacocktv.com/lightning/release/prod/tizen/7.10.20-ltv/bundle.main.f0238d0fa6ccdb8f0e96.js), SHA256 `4baa669160eedf20d20ce4944222972e0f83f46a30ddc597583202d8e241f28d`.
- [Routing worker](https://tv.clients.peacocktv.com/lightning/release/prod/tizen/7.10.20-ltv/chunk.worker.core.f0238d0fa6ccdb8f0e96.js), SHA256 `e8d9bd80d1915744b627b0f12f6e4ba8ab4ea7ef24b2fc34fca8ac9e6e47e9db`.
- [Pilot's public page](https://www.peacocktv.com/watch-online/tv/the-office/4902514835143843112/seasons/1/episodes/pilot-episode-1/58fabbf4-9291-3575-bcdc-bd25eee192c8), whose page data confirms season 1, episode 1 and title.

Profile selection, ads, availability and subscriptions remain app-controlled.
Stop at login or offers; an accepted payload does not verify playback.

## Crunchyroll

Crunchyroll's installed entrypoint is `https://www.crunchyroll.com/tetsuo/tizen-14/`;
an unauthenticated fetch returned HTTP error, so its app parser was not inspected.
Public watch pages expose episode IDs, but multiple IDs can describe the same
episode title. Resolve season, episode and requested audio edition before using
one. Example: [Frieren S1E1](https://www.crunchyroll.com/watch/G0DUND0K2/the-journeys-end).
[Crunchyroll's Samsung guide](https://help.crunchyroll.com/hc/en-us/articles/19694586156180-Crunchyroll-App-for-Samsung-Smart-TV)
explicitly says Samsung Smart View is unsupported; do not promise casting as a
substitute for native app control.

## Remaining validation

Peacock's submitted play request still needs owner confirmation; its standalone series
command has not been separately screen-tested. Crunchyroll requires owner login first. Each new route needs a known target,
a bounded request, and observed identity/playback before promotion to supported
commands. Root was used for static inspection and temporary UI captures only;
no app patch or persistent root controller was installed.
