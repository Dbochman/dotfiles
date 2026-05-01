# rest980 — Roomba MQTT bridges (MBP only)

Runs on `dylans-macbook-pro` (Crosstown LAN). Each `start-*.sh` is a launchd
wrapper around the rest980 Express app at `~/.openclaw/rest980-app/`, scoped
to one Roomba via a private env file.

## Tracked here
- `start-10max.sh`, `start-j5.sh` — launchd entrypoints, autodetect node path
- `roomba-cmd.js` — direct dorita980 wrapper for ad-hoc CLI use
- Plists live in `openclaw/launchagents/com.openclaw.rest980-{10max,j5}.plist`

## NOT tracked (live on MBP only)
- `env-10max`, `env-j5` — Roomba blid + password (chmod 600). Roombas are
  paired once via `dorita980 getRobotPublicInfo`; rotating the credentials
  requires re-pairing, so these are local-only by design.
- `node_modules/`, `package.json`, `package-lock.json` — npm runtime,
  regenerable via `npm install` against a vendored package.json.

## Deployment
`dotfiles-pull.command` (run by Mini's launchd) syncs the tracked scripts
to MBP via Tailscale scp on each pull. To take a script change live:
`launchctl bootout gui/$(id -u) com.openclaw.rest980-10max && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.openclaw.rest980-10max.plist`
(or just `kickstart -k` if the plist itself didn't change).
