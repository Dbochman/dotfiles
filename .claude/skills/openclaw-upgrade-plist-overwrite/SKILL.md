---
name: openclaw-upgrade-plist-overwrite
description: >-
  Diagnose and repair the OpenClaw gateway LaunchAgent after an upgrade or
  service reinstall changes its plist, wrapper, cached environment, or logs.
---

# OpenClaw Gateway LaunchAgent Repair

## Current healthy contract

OpenClaw `2026.6.10` may install the live gateway as a generated regular plist,
even though `install.sh` can initially create a symlink to the tracked recovery
plist. The generated form is healthy when its `ProgramArguments` chain is:

1. `~/.openclaw/service-env/ai.openclaw.gateway-env-wrapper.sh`
2. `~/.openclaw/service-env/ai.openclaw.gateway.env`
3. `~/Applications/OpenClawGateway.app/Contents/MacOS/OpenClawGateway`

The mode-`0600` environment file supplies the gateway token and service
metadata. The FDA app wrapper sources `~/.openclaw/.secrets-cache`, uses the
Homebrew Node runtime, and keeps `/usr/sbin` on `PATH` so gateway helpers can
find the active macOS Tailscale backend.

The tracked plist at
`~/dotfiles/openclaw/launchagents/ai.openclaw.gateway.plist` is a direct-wrapper
recovery source. It is not expected to be byte-identical to the generated live
plist.

## Trigger conditions

- Gateway crash-looping or absent after an OpenClaw upgrade
- Native iMessage, Control UI, cron, or Tailscale Serve unavailable together
- `MissingEnvVarError`, config validation errors, or an unavailable gateway token
- The live plist bypasses both the service-environment wrapper and the FDA app
- The service environment has an incomplete `PATH`, especially no `/usr/sbin`

## Diagnose

```bash
launchctl print gui/$(id -u)/ai.openclaw.gateway
plutil -p ~/Library/LaunchAgents/ai.openclaw.gateway.plist
```

Read the log path from the live plist. The current generated service uses:

```bash
tail -80 ~/Library/Logs/openclaw/gateway.log
```

The tracked recovery plist instead names
`~/.openclaw/logs/gateway.{log,err.log}`. Do not assume an old path is current.

Inspect environment keys without printing secret values:

```bash
awk -F= '/^export / {sub(/^export /, "", $1); print $1}' \
  ~/.openclaw/service-env/ai.openclaw.gateway.env
grep '^export PATH=' ~/.openclaw/service-env/ai.openclaw.gateway.env
```

Require `OPENCLAW_GATEWAY_TOKEN`, the service metadata keys, and a `PATH` that
contains `/opt/homebrew/bin`, `/opt/homebrew/sbin`, `/usr/sbin`, and `/sbin`.

## Preferred repair: regenerate around the FDA wrapper

Back up the live contract, source the cache, and ask OpenClaw to regenerate the
service with the explicit wrapper:

```bash
cp ~/Library/LaunchAgents/ai.openclaw.gateway.plist \
  ~/Library/LaunchAgents/ai.openclaw.gateway.plist.pre-repair

set -a
source ~/.openclaw/.secrets-cache
set +a

openclaw gateway install --force \
  --port 18789 \
  --token "$OPENCLAW_GATEWAY_TOKEN" \
  --wrapper "$HOME/Applications/OpenClawGateway.app/Contents/MacOS/OpenClawGateway"
```

If the generated environment omits the system paths, repair its `PATH` to match
the tracked app wrapper before restarting. Never put a literal token in the
tracked repository plist.

## Recovery fallback

If service generation itself is broken, install the tracked direct-wrapper
plist temporarily:

```bash
launchctl bootout gui/$(id -u)/ai.openclaw.gateway 2>/dev/null || true
cp ~/dotfiles/openclaw/launchagents/ai.openclaw.gateway.plist \
  ~/Library/LaunchAgents/ai.openclaw.gateway.plist
chmod 600 ~/Library/LaunchAgents/ai.openclaw.gateway.plist
plutil -lint ~/Library/LaunchAgents/ai.openclaw.gateway.plist
launchctl bootstrap gui/$(id -u) \
  ~/Library/LaunchAgents/ai.openclaw.gateway.plist
```

The FDA app wrapper remains the execution boundary in both forms. Confirm its
installed OpenClaw entry point after every version change rather than assuming a
historical `dist/index.js` or `dist/entry.js` path.

## Verify

```bash
launchctl print gui/$(id -u)/ai.openclaw.gateway | \
  grep -E 'state =|pid =|last exit code ='
curl -fsS http://127.0.0.1:18789/health
openclaw channels status --probe --channel imessage
imsg status --json
tailscale serve status
```

Success requires a running replacement PID, HTTP health `status: live`, one
attached native `imsg rpc` worker, a healthy iMessage probe, and the expected
tailnet-only Serve route. A plist edit requires `bootout` plus `bootstrap` to
reload its launch contract; `kickstart` is sufficient only when the loaded plist
itself did not change.

## Upgrade safeguard

The weekly auto-upgrade LaunchAgent is retired. During a manual upgrade:

1. Copy the live plist and service-environment files before installing.
2. Upgrade OpenClaw.
3. Compare the generated contract and app-wrapper entry point.
4. Regenerate with `openclaw gateway install --force --wrapper ...` when needed.
5. Reapply the complete system `PATH`, restart, and run every verification above.

Do not restore an old plist blindly: service wrapper arguments, environment
format, entry points, and log locations can legitimately change between
OpenClaw releases.
