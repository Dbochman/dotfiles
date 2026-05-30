# cmux Remote Sessions

Detailed reference for opening persistent remote shells through `cmux`, SSH, Tailscale, and remote `tmux`.

See also: [remote-access.md](remote-access.md) for the tailnet host inventory and tmux key bindings.

## Mental Model

There are three layers:

| Layer | Runs where | Purpose |
| --- | --- | --- |
| `cmux` | Local Mac | Owns the local workspace/tab and the managed SSH connection. |
| `ssh` | Local Mac to remote host | Authenticates and transports the shell/remote command. |
| `tmux` | Remote host | Keeps shells and long-running work alive after SSH disconnects. |

Use `cmux` for the local workspace, and use `tmux` for durability on the remote machine.

## Command Shape

```bash
cmux ssh <ssh-target> --name "<workspace-name>" --ssh-option RequestTTY=force -- <remote-command>
```

Important details:

- `<ssh-target>` should usually be an SSH config alias, not a raw Tailscale IP.
- `--name` gives the cmux workspace a stable title.
- `--ssh-option RequestTTY=force` is required for remote commands like `tmux`.
- Everything after `--` is the command run on the remote host.

## Canonical Targets

These are the local SSH aliases that matter for cmux remote sessions:

| Alias | Remote | User | Notes |
| --- | --- | --- | --- |
| `work-mac` | `100.73.15.5` | `dbochman` | Work MBP by Tailscale IP, with `~/.ssh/id_work_mbp`. |
| `work-mbp` | `work-mbp.tail3e55f9.ts.net` | `dbochman` | Work MBP by MagicDNS, with `~/.ssh/id_work_mbp`. |
| `dylans-work-mbp` | `100.73.15.5` | `dbochman` | Work MBP generic alias; uses dedicated key via `Match`. |
| `devc` | `127.0.0.1:22390` | `root` | Devcontainer reached through `work-mac` as a proxy. |
| `dylans-mac-mini` | Tailscale/MagicDNS | `dbochman` | Mac Mini remote shell target. |

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

Underlying command:

```bash
cmux ssh dylans-mac-mini --name home --ssh-option RequestTTY=force -- \
  /opt/homebrew/bin/tmux new-session -A -s home
```

## Adding a New cmux Wrapper

Use this pattern for new durable remote sessions:

```bash
my-session() {
  _cmux_ensure_running || return

  cmux ssh <ssh-target> --name <workspace-name> --ssh-option RequestTTY=force "$@" -- \
    tmux new-session -A -s <tmux-session-name> || \
    cmux new-workspace --name <workspace-name> \
      --command "ssh -t <ssh-target> tmux new-session -A -s <tmux-session-name>"
}
```

Rules of thumb:

- Use `tmux new-session -A -s <name>` when the session should self-create after reboot.
- Use `tmux attach -t <name>` only when a missing session should be treated as a real error.
- Keep the cmux workspace name and tmux session name the same unless there is a clear reason not to.
- Include `"$@"` before `--` so one-off `cmux ssh` flags can still be passed through.

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

`tmux` needs a TTY. Add:

```bash
--ssh-option RequestTTY=force
```

For plain `ssh`, the equivalent is:

```bash
ssh -t work-mac 'tmux new-session -A -s work'
```

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
```
