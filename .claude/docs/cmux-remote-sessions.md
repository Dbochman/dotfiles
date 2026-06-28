# cmux Remote Sessions

Detailed reference for opening persistent remote shells through `cmux`, SSH, Tailscale, and remote `tmux`.

See also: [remote-access.md](remote-access.md) for the tailnet host inventory and tmux key bindings.

## Mental Model

There are three layers:

| Layer | Runs where | Purpose |
| --- | --- | --- |
| `cmux` | Local Mac | Owns the local workspace/tab. Native remote workspaces may also manage SSH. |
| `ssh` | Local Mac to remote host | Authenticates and transports the shell/remote command. |
| `tmux` | Remote host | Keeps shells and long-running work alive after SSH disconnects. |

Use `cmux` for the local workspace, and use `tmux` for durability on the remote machine.

## Connection Modes

### Native cmux SSH

```bash
cmux ssh <ssh-target> --name "<workspace-name>" --ssh-option RequestTTY=force -- <remote-command>
```

Important details:

- `<ssh-target>` should usually be an SSH config alias, not a raw Tailscale IP.
- `--name` gives the cmux workspace a stable title.
- `--ssh-option RequestTTY=force` is required for remote commands like `tmux`.
- Everything after `--` is the command run on the remote host.

Native remote workspaces provide cmux's remote browser proxy, drag-and-drop upload, CLI relay, and managed reconnection. In cmux 0.64.17, however, forcing a TTY on the workspace can also affect background daemon/platform probes. Use the reconnecting wrapper below for the Mac Mini `home` session, where sleep durability matters more than those managed-remote features.

### Reconnecting SSH + tmux

```bash
cmux-ssh-tmux <ssh-target> <tmux-session-name> [tmux-binary]
```

The local helper:

- runs `ssh -tt` inside a normal cmux workspace;
- uses `ServerAliveInterval=10` and `ServerAliveCountMax=3` to detect a broken connection in roughly 30 seconds;
- retries only OpenSSH status 255 with bounded backoff;
- exits normally when tmux detaches cleanly; and
- registers a signed cmux surface resume command for full app relaunches.

This avoids coupling tmux's foreground TTY to cmux's background remote-daemon probes. The wrapper also disables SSH multiplexing for its connection so it cannot reuse a stale control socket.

## Canonical Targets

These are the local SSH aliases that matter for cmux remote sessions:

| Alias | Remote | User | Notes |
| --- | --- | --- | --- |
| `work-mac` | `work-mbp.tail3e55f9.ts.net` | `dbochman` | Work MBP by MagicDNS, with `~/.ssh/id_work_mbp`. |
| `work-mbp` | `work-mbp.tail3e55f9.ts.net` | `dbochman` | Work MBP by MagicDNS, with `~/.ssh/id_work_mbp`. |
| `dylans-work-mbp` | `100.73.15.5` | `dbochman` | Work MBP generic alias; uses dedicated key via `Match`. |
| `devc` | `127.0.0.1:22390` | `root` | Devcontainer reached through `work-mac` as a proxy. |
| `dylans-mac-mini` | Tailscale/MagicDNS | `dbochman` | Mac Mini remote shell target. |
| `mac-mini` | `dylans-mac-mini.tail3e55f9.ts.net` | `dbochman` | Same Mac Mini with the dedicated `~/.ssh/id_mac_mini` key. |

Prefer `work-mac` or `work-mbp` over `dbochman@100.73.15.5`. The aliases select the right key and bypass the 1Password agent.

## Current Workflows

### Work MBP tmux Session

Use the local wrapper:

```bash
opwork
```

Attach to or create the `work` tmux session on the work MBP:

```bash
cmux ssh work-mac --name "op-research work" --ssh-option RequestTTY=force -- \
  tmux new-session -A -s work
```

The equivalent MagicDNS version is:

```bash
cmux ssh work-mbp --name "op-research work" --ssh-option RequestTTY=force -- \
  tmux new-session -A -s work
```

If you use the raw IP, pass the key explicitly:

```bash
cmux ssh dbochman@100.73.15.5 --identity ~/.ssh/id_work_mbp \
  --name "op-research work" --ssh-option RequestTTY=force -- \
  tmux new-session -A -s work
```

### Knowledge Base Devcontainer

The `kb` shell function opens a cmux workspace named `lpu-kb` and attaches to the existing `lpu-kb` tmux session on `devc`:

```bash
kb
```

Underlying command:

```bash
cmux ssh devc --name lpu-kb --ssh-option RequestTTY=force -- \
  tmux attach -t lpu-kb
```

This session must already exist on the devcontainer. If it was killed, recreate it from a shell on `devc`:

```bash
tmux new -s lpu-kb
```

### Mac Mini Home Session

The `home` shell function opens a cmux workspace named `home` and attaches to or creates the `home` tmux session on the Mac Mini:

```bash
home
```

Underlying workspace command:

```bash
$HOME/.local/bin/cmux-ssh-tmux \
  dylans-mac-mini home /opt/homebrew/bin/tmux
```

`home --no-focus` creates the workspace with `--focus false`. Other supported cmux workspace-creation flags are passed through.

On the first durable workspace, cmux shows **Auto-Restore / Ask Each Time / Keep Manual** for the exact helper command. Choose **Auto-Restore** only after checking the executable, host, session, and tmux path. cmux 0.64.17's **Settings > Terminal > Resume Commands** row only opens `cmux.json`; do not change a stored policy by hand because approval records are HMAC-signed.

### Relay-Enabled Codex Orchestration

The `home` wrapper above optimizes for a durable terminal. It is not the right bootstrap for an agent that must control cmux itself from the Mac Mini.

In cmux 0.64.17, supplying `tmux` as the command after `--` skips the normal interactive remote bootstrap. The terminal can still work, but the remote process may not receive a usable cmux relay. This was observed as:

```text
Remote daemon bootstrap failed
failed to query remote platform
```

Use a clean managed SSH workspace first, then attach tmux from the remote prompt:

```bash
# Run on dylans-mac, outside an existing SSH session.
cmux ssh dylans-mac-mini --name "Codex Orchestrator"

# Run at the new Mac Mini prompt.
~/.cmux/bin/cmux ping
tmux new-session -A -s home
```

The relay health check must return `PONG`. If `home` already contains the orchestrator, attaching returns to the live Codex process. If the process is gone, use the Codex resume picker to reopen its saved transcript:

```bash
codex resume
```

The verified topology is:

```text
dylans-mac
└── cmux workspace: Codex Orchestrator (renamed and pinned)
    └── managed SSH connection and authenticated relay
        └── dylans-mac-mini
            └── tmux session: home
                └── long-running Codex orchestration thread
```

The current workspace also has a manual cmux surface-resume binding for:

```bash
tmux attach-session -t home
```

cmux stores this binding with the workspace snapshot. It remains manual because CLI-created resume commands require explicit approval; do not bypass that trust boundary by editing relay or approval state.

#### What the Orchestrator Can Control

Through the authenticated relay, the orchestrator can:

- list and select cmux workspaces and terminal surfaces;
- read the visible text of a specifically targeted surface;
- send text or key presses to a specifically targeted surface;
- create an isolated workspace or split, launch Codex there, and monitor its output; and
- restore focus and close only workspaces that it created.

These Codex processes are independent CLI sessions, not structured subagents of the orchestrator thread. They do not automatically share prompts, memory, approvals, or completion state.

Use the following operating contract:

- Give every agent an explicit absolute working directory before launching it.
- Use separate worktrees when multiple agents will modify the same repository concurrently.
- Target immutable workspace and surface IDs; do not rely on whichever pane happens to be focused.
- Verify the workspace title and working directory before sending input.
- Read only the intended agent surface, not unrelated tabs.
- Do not answer permission prompts or destructive confirmations without explicit authorization.

A simple launch pattern inside a dedicated agent workspace is:

```bash
cd -- /absolute/path/to/project
codex
```

#### Persistence Layers and Limits

| Layer | What persists | Limit |
| --- | --- | --- |
| cmux workspace | Title, pin state, layout, working directory, and best-effort scrollback. | Does not checkpoint arbitrary process memory. |
| Managed SSH | Reconnects after transient connection loss and re-establishes the relay. | Does not survive the remote host rebooting. |
| Remote tmux | Keeps the live Codex process running across SSH or cmux disconnects. | Ends if the Mac Mini or tmux server stops. |
| Codex transcript | Allows `codex resume` after the live process is gone. | Automatic resume needs a compatible cmux Codex hook integration. |

`terminal.autoResumeAgentSessions` is currently unset, so cmux's default of `true` applies. However, installed cmux 0.64.17 does not provide the newer `cmux hooks setup codex` command, and `identify` is also unavailable. Do not hand-edit Codex hook files to imitate a newer cmux release. Until cmux is upgraded and the integration is verified, rely on tmux for live-process continuity and `codex resume` for transcript recovery.

Remote-tmux beta and agent hibernation are not required for this setup and remain disabled.

#### Relay Health and Recovery

From the Mac Mini orchestration thread:

```bash
test -x ~/.cmux/bin/cmux
test -e ~/.cmux/socket_addr
~/.cmux/bin/cmux ping
~/.cmux/bin/cmux capabilities --json
tmux display-message -p '#S'
```

Expected results are an executable helper, a relay address, `PONG`, a capabilities response, and tmux session `home`. The helper may not be on `PATH` inside an older tmux process, so use its absolute path. Never print or inspect relay authentication files.

If the relay check fails:

1. Leave the failed cmux workspace intact long enough to capture its error.
2. Open a new local cmux tab.
3. Run `cmux ssh dylans-mac-mini --name "Codex Orchestrator"` without a command after `--`.
4. Confirm `~/.cmux/bin/cmux ping` returns `PONG` before attaching tmux.
5. Attach `home` and resume Codex only if no live process remains.

Upstream references: [cmux SSH](https://cmux.com/docs/ssh), [CLI and socket API](https://cmux.com/docs/api), and [session restore](https://cmux.com/docs/session-restore).

## Adding a New cmux Wrapper

Use this pattern for new durable remote sessions:

```bash
my-session() {
  _cmux_ssh_tmux \
    <workspace-name> <ssh-target> <tmux-session-name> <tmux-binary> "$@"
}
```

Rules of thumb:

- Use `tmux new-session -A -s <name>` when the session should self-create after reboot.
- Use `tmux attach -t <name>` only when a missing session should be treated as a real error.
- Keep the cmux workspace name and tmux session name the same unless there is a clear reason not to.
- Keep native `cmux ssh` when the remote browser proxy, uploads, or CLI relay are required and the target works with cmux's managed daemon.
- Use `_cmux_ssh_tmux` when remote tmux continuity across local sleep and network changes is the priority.

## Health Checks

Check that cmux is installed:

```bash
command -v cmux
cmux ssh --help
```

Check that the cmux app/socket is reachable:

```bash
cmux ping
```

Launch cmux if needed:

```bash
open /Applications/cmux.app
```

Check that Tailscale sees the host:

```bash
tailscale status | grep work-mbp
```

Check what SSH config will actually use:

```bash
ssh -G work-mac | egrep '^(user|hostname|identityfile|identitiesonly|identityagent|requesttty) '
```

For the Mac Mini aliases, also verify bounded failure detection:

```bash
ssh -G dylans-mac-mini | \
  egrep '^(connecttimeout|connectionattempts|serveraliveinterval|serveralivecountmax) '
ssh -G mac-mini | \
  egrep '^(connecttimeout|connectionattempts|serveraliveinterval|serveralivecountmax) '
```

Check key-based auth without prompting:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=5 work-mac true
```

## Troubleshooting

### SSH Asks for a Password

The most common cause is using a raw IP instead of an SSH config alias:

```bash
# Avoid this unless you also pass --identity.
cmux ssh dbochman@100.73.15.5 ...

# Prefer this.
cmux ssh work-mac ...
```

The raw IP does not match the `work-mac` or `work-mbp` SSH config blocks, so SSH does not automatically use `~/.ssh/id_work_mbp`.

Verify the alias:

```bash
ssh -G work-mac | grep identityfile
ssh -o BatchMode=yes work-mac true
```

### `open terminal failed: not a terminal`

`tmux` needs a TTY. For a reconnecting durable session, use:

```bash
cmux-ssh-tmux <ssh-target> <tmux-session-name> [tmux-binary]
```

For one-off plain `ssh`, the equivalent is:

```bash
ssh -tt work-mac 'tmux new-session -A -s work'
```

For a native managed cmux workspace, `--ssh-option RequestTTY=force` supplies the TTY but may reproduce the cmux 0.64.17 background-probe conflict described above.

### Workspace Stalls After Laptop Sleep

First confirm tmux survived:

```bash
ssh <ssh-target> 'tmux list-sessions'
```

Then inspect cmux and the foreground SSH process:

```bash
cmux top --all --processes --flat
cmux ssh-session-list --all-workspaces
cmux read-screen --workspace <ref> --scrollback --lines 200
```

If the remote tmux session exists but a native workspace repeatedly reports `Remote daemon bootstrap failed`, close only the stale local workspace and reopen it through `_cmux_ssh_tmux`. Do not kill the remote tmux session.

### `cmux: command not found`

The CLI shim is missing from `PATH`. The expected binary is:

```bash
/Applications/cmux.app/Contents/Resources/bin/cmux
```

Reinstall cmux or add/symlink the binary into a directory already on `PATH`.

### `cmux ping` Fails

The app/socket is not up yet:

```bash
open /Applications/cmux.app
cmux ping
```

If it still fails, check the cmux app itself and reload config if needed:

```bash
cmux reload-config
```

### Host Is Not Reachable

Confirm the Tailscale side first:

```bash
tailscale status
tailscale ping work-mbp
```

Then confirm SSH independently of cmux:

```bash
ssh work-mac true
ssh work-mbp true
```

If plain SSH fails, fix SSH/Tailscale before debugging cmux.

### Workspace Opens but tmux Session Is Missing

For self-healing sessions, use:

```bash
tmux new-session -A -s <name>
```

For attach-only sessions, create the session manually:

```bash
tmux new -s <name>
```

### Kill a Remote tmux Session

From inside the session:

```bash
tmux kill-session -t <name>
```

From local:

```bash
ssh <ssh-target> 'tmux kill-session -t <name>'
```

## Quick Reference

```bash
# Work MBP durable session.
cmux ssh work-mac --name "op-research work" --ssh-option RequestTTY=force -- \
  tmux new-session -A -s work

# Same target by MagicDNS alias.
cmux ssh work-mbp --name "op-research work" --ssh-option RequestTTY=force -- \
  tmux new-session -A -s work

# Raw-IP fallback with explicit key.
cmux ssh dbochman@100.73.15.5 --identity ~/.ssh/id_work_mbp \
  --name "op-research work" --ssh-option RequestTTY=force -- \
  tmux new-session -A -s work

# Existing local wrappers.
opwork
kb
home

# Direct reconnecting Mac Mini attach.
cmux-ssh-tmux dylans-mac-mini home /opt/homebrew/bin/tmux
```
