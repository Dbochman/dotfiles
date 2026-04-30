---
name: tailscale-macos-localapi-stale-port
description: |
  Fix Tailscale CLI on macOS failing with "Failed to connect to local Tailscale daemon
  for /localapi/v0/status... dial tcp 127.0.0.1:PORT: connect: can't assign requested
  address" while Tailscale.app appears to be running normally. Use when:
  (1) every `tailscale` CLI command (status, file cp, ip, set, etc.) fails with the
  stale-port LocalAPI error, (2) Tailscale.app is in `pgrep` output and other nodes
  can still see this host in `tailscale status` from elsewhere (control plane cache
  is misleading), (3) a launchd/cron script that uses `tailscale file cp` or other
  CLI calls has been silently failing for hours — especially when the script suppresses
  stderr with `2>/dev/null` and downstream sticky-state consumers keep showing stale
  values, (4) `lsof -nP -p $TAILSCALE_PID` shows almost no FDs (no listening sockets)
  even though the process is alive. The standalone macOS GUI Tailscale.app exposes
  its LocalAPI on a TCP loopback port that the bundled CLI discovers via the GUI's
  in-process state. After certain Tailscale.app updates or partial-crash states, the
  GUI keeps running but stops listening on the LocalAPI port the CLI cached, so every
  CLI call fails. Fix is to restart Tailscale.app.
author: Claude Code
version: 1.0.0
date: 2026-04-29
---

# Tailscale macOS LocalAPI Stale Port

## Problem

The standalone macOS Tailscale.app (bundle ID `io.tailscale.ipn.macsys`, installed
from tailscale.com — not the Mac App Store version) bundles its CLI and daemon
in one binary. The CLI talks to the in-process daemon over a TCP LocalAPI on a
random loopback port. After certain Tailscale.app updates or partial-crash states,
the GUI process keeps running and Tailscale traffic keeps flowing (so other nodes
still see this host in `tailscale status`) but the LocalAPI listener is gone. Every
`tailscale` CLI invocation then fails with the same stale-port error, breaking
anything that shells out to the CLI — including launchd jobs that use
`tailscale file cp` (Taildrop), `tailscale ip`, `tailscale set`, etc.

## Context / Trigger Conditions

CLI returns this error on every command:

```
Failed to connect to local Tailscale daemon for /localapi/v0/status; not running?
Error: dial tcp 127.0.0.1:53755: connect: can't assign requested address
```

(The port number varies — whatever the CLI cached.)

All of these are simultaneously true:

- `pgrep -f Tailscale` shows the GUI app running
- `tailscale status` run from a *different* Tailscale node shows this host as
  reachable, possibly with `idle, tx N rx M` (the control plane has cached the
  registration; bytes counters can look stale)
- `lsof -nP -p $TAILSCALE_PID` shows almost no FDs — no `LISTEN` sockets, no
  unix sockets, just `cwd` and `txt`. This is the smoking gun that the process
  is half-dead.
- `lsof -nP -iTCP -sTCP:LISTEN | grep -i tailscale` returns nothing
- The CLI under `/usr/local/bin/tailscale` is just a shim:
  `#!/bin/sh\n/Applications/Tailscale.app/Contents/MacOS/tailscale "$@"` — using
  the bundled binary directly produces the same error
- `~/Library/Containers/io.tailscale.ipn.macsys/Data/` may not exist (standalone
  build doesn't always create it)

A common compounding factor: the failing command is invoked from a launchd cron
script that suppresses stderr (`tailscale file cp ... 2>/dev/null`), so the
failure is silent. Downstream consumers that depend on the pushed file (e.g. a
sticky-state presence model that holds a person's location until "detected at
the other location") then continue using the last successfully-pushed value for
hours or days, causing silent misattribution.

## Solution

### Primary fix: restart Tailscale.app

From a GUI session on the affected Mac (menu bar icon → Quit, then re-open),
or via SSH if a GUI session is logged in:

```bash
killall Tailscale
sleep 2
open -a Tailscale
```

`open -a` from an SSH session works only if a GUI session is logged in (which
is the normal case for a Mac running Tailscale.app continuously). If `open`
silently no-ops, fall back to:

```bash
launchctl asuser $(id -u) open -a Tailscale
```

Or VNC in and click the menu bar icon. After the restart, verify:

```bash
tailscale status      # should print peer list, not the LocalAPI error
tailscale ip -4       # should print this host's tailnet IP
```

### Caveat: bootstrap trap — your remote path may go over Tailscale

If you are SSH'd or VNC'd into the affected Mac *via its Tailscale IP/hostname*,
killing Tailscale severs your own control channel before the relaunch step.
This is the most common way to brick yourself out of the affected host:

- **VNC via Tailscale + Quit from menubar**: the Quit action lands, Tailscale
  exits, your VNC session dies, and you cannot click "open Tailscale.app"
  remotely. The Mac now needs physical or non-Tailscale-network access.
- **SSH via Tailscale + `killall Tailscale; open -a Tailscale`**: SSH dies
  mid-command. The shell's pgroup ends with the connection, so `open -a`
  may or may not have run depending on shell options (`huponexit`).

To avoid the trap:

- **Use a non-Tailscale path for the recovery session.** macOS built-in
  Screen Sharing over Apple ID relay (System Settings → General → Sharing →
  Screen Sharing → "Allow for Apple Account") works without Tailscale and
  is the cleanest fallback. Local LAN, Bonjour `.local`, or a wired
  console also work.
- **If you must go over Tailscale**, plan the kill+restart as one self-contained
  command with `nohup` and detach so it survives session death:
  ```bash
  ssh affected-mac 'nohup sh -c "sleep 1; killall Tailscale; sleep 3; open -a Tailscale" >/dev/null 2>&1 </dev/null & disown' 
  ```
  Then wait ~30s and reconnect to verify. Test this on a non-critical host
  first — if `open -a` fails (no GUI session, login items disabled, etc.),
  the Mac stays Tailscale-less and you still need physical access.

If you're already locked out: someone with physical or local-LAN access to
the Mac just needs to Cmd+Space → "Tailscale" → Enter. The login-item-helper
also relaunches Tailscale on next user login, so a reboot via smart plug
recovers it (heavy hammer).

### Cleanup: stuck CLI invocations

`tailscale file cp` hangs indefinitely when the LocalAPI is unreachable
(rather than failing fast in some versions). If the cron job has been firing
every ~30s for hours, you'll have many stuck `tailscale file cp` processes.
Kill them after the GUI restart:

```bash
pkill -f 'tailscale file cp'
```

### Hardening: don't suppress stderr

Once functional again, change the script that calls `tailscale file cp` to
log stderr instead of discarding it. The `2>/dev/null` pattern is the reason
this regression went undetected:

```bash
# before
echo "$result" | tailscale file cp - dylans-mac-mini: 2>/dev/null && \
  log "Pushed" || log "WARN: Failed to push"

# after — surface the actual error
err=$(echo "$result" | tailscale file cp - dylans-mac-mini: 2>&1 >/dev/null)
if [ $? -eq 0 ]; then
  log "Pushed"
else
  log "WARN: Failed to push: $err"
fi
```

## Verification

After restart, on the affected Mac:

1. `tailscale status` returns the peer list with no LocalAPI error
2. `lsof -nP -p $(pgrep -fn 'Tailscale.app/Contents/MacOS/Tailscale') | grep -c LISTEN`
   returns a non-zero count (at least one LISTEN socket — the LocalAPI)
3. A round-trip Taildrop test succeeds: `echo test | tailscale file cp - <peer>:`
   returns exit 0 and the peer receives the file

Downstream: the Mini's `crosstown-scan.json` (or whatever the consumer reads)
gets a fresh mtime within one push interval.

## Notes

- **Don't trust remote `tailscale status` as a health signal.** The control
  plane caches each node's registration and reports `idle, tx X rx Y` even
  when the node's local CLI/LocalAPI is broken. The only reliable signal is
  running a CLI command on the affected host itself.
- This is specific to the **standalone** macOS Tailscale (`io.tailscale.ipn.macsys`,
  downloaded from tailscale.com). The Mac App Store build (`io.tailscale.ipn.macos`)
  is sandboxed differently and doesn't expose the CLI the same way. The Homebrew
  `tailscaled` formula is yet another path and requires `sudo` — Homebrew's
  `homebrew.mxcl.tailscale.plist` LaunchAgent runs as the user and crash-loops
  with `tailscaled requires root; use sudo tailscaled` if accidentally enabled
  alongside the GUI app.
- If `lsof` shows the Tailscale.app process has FDs (sockets, files) but the
  CLI still fails, this is *not* the stale-port scenario — likely a different
  bug. Check `~/Library/Logs/Tailscale/` for crashes.
- Long-term: file an issue with Tailscale if you can reproduce. The half-dead
  state (process alive, listener gone) is a Tailscale.app bug, not a misuse.

## References

- [Tailscale macOS client documentation](https://tailscale.com/kb/1016/macos-installation)
- [Taildrop documentation](https://tailscale.com/kb/1106/taildrop)
- macOS standalone build bundle ID: `io.tailscale.ipn.macsys`
