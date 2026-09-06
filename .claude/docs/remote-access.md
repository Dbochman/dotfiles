# tmux + Mobile Remote Access

Quick reference for accessing machines remotely via tmux + Tailscale.

For cmux-managed remote workspaces, see [cmux-remote-sessions.md](cmux-remote-sessions.md).

## Tailnet Devices

| Device | Tailscale IP | Hostname | User | OS | Role |
|--------|-------------|----------|------|----|------|
| Mac (primary) | `100.94.69.122` | `dylans-mac` | `dylanbochman` | macOS | Development machine |
| Mac Mini | `100.104.114.1` | `dylans-mac-mini` | `dbochman` | macOS | OpenClaw gateway, cabin server |
| RTX 5090 desktop | `100.121.232.95` | `desktop-r9js0ok` | `openclaw` (Ubuntu WSL) | Windows + WSL 2 | General remote compute |
| MacBook Pro | `100.107.209.85` | `dylans-macbook-pro` | `dbochman` | macOS | Crosstown presence scanner |
| Work MBP | `100.73.15.5` | `work-mbp` / `work-mac` | `dbochman` | macOS | Work laptop, devcontainer proxy |
| Andre droplet | `100.92.192.62` | `andre` | `deploy` | Linux | Personal website hosting |
| iPhone | `100.93.90.122` | `iphone171` | — | iOS | Mobile access |

SSH from any tailnet device: `ssh dylans-mac`, `ssh dylans-mac-mini`, `ssh dylans-macbook-pro`, or `ssh work-mac`.

The RTX 5090 desktop is different: it maintains a reverse SSH tunnel into the
Mac Mini. From the Mini, use `ssh desktop-compute`; from another device, connect
to the Mini first. The desktop's raw Tailscale IP is not an SSH endpoint.

## tmux Quick Reference

### Key Bindings (prefix: Ctrl-a)

| Action | Keys |
|--------|------|
| Detach | `Ctrl-a d` |
| Split vertical | `Ctrl-a \|` |
| Split horizontal | `Ctrl-a -` |
| Switch panes | `Alt + arrow` (no prefix) |
| Kill pane | `Ctrl-a k` |
| Kill window | `Ctrl-a K` |
| Reload config | `Ctrl-a r` |
| Scroll mode | Mouse scroll or `Ctrl-a [` |

### Common Workflows

**Start a session before leaving desk:**
```bash
tmux new -s work
# do stuff, or leave something running
# Ctrl-a d to detach
```

**Reconnect from phone:**
```bash
tmux attach -t work
```

**Run or resume work on the RTX 5090 desktop from the Mac Mini:**

```bash
ssh -tt desktop-compute 'exec tmux new-session -A -s compute'
```

**List sessions:**
```bash
tmux list-sessions
```

## Config

- tmux config: `~/.tmux.conf` (Ctrl-a prefix, mouse support, mobile-friendly)
- Auth: 1Password SSH agent (key-based)
- Desktop compute auth: dedicated Mini-local key and a loopback-only reverse
  SSH bridge; no password or public listener. Because WSL Windows interop is
  enabled, treat the key as administrator-equivalent even though the account
  has no Linux `sudo` membership.

## Troubleshooting

**"not a terminal" error:**
tmux needs a real terminal. Won't work from Claude Code's bash tool or scripts without a TTY.

**Can't connect via Tailscale:**
- Check `tailscale status` on both devices
- Ensure both are logged into same tailnet
- Try `tailscale ping <hostname>` from phone

**Can't connect to `desktop-compute`:**

- Run the command from the Mac Mini, where the reverse listener terminates.
- Check `lsof -nP -iTCP:22022 -sTCP:LISTEN` on the Mini.
- If absent, rerun the desktop's `OpenClaw WSL SSH Bridge.cmd` Startup command.
