# tmux + Mobile Remote Access

Quick reference for accessing machines remotely via tmux + Tailscale.

## Tailnet Devices

| Device | Tailscale IP | Hostname | User | OS |
|--------|-------------|----------|------|----|
| Mac (primary) | `100.94.69.122` | `dylans-mac` | `dylanbochman` | macOS |
| Mac Mini | `100.93.66.71` | `dylans-mac-mini` | `dbochman` | macOS |
| Andre droplet | `100.92.192.62` | `andre` | `deploy` | Linux |
| iPhone | `100.93.90.122` | `iphone171` | — | iOS |

SSH from any tailnet device: `ssh dylans-mac` or `ssh dylans-mac-mini`

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

**List sessions:**
```bash
tmux list-sessions
```

## Config

- tmux config: `~/.tmux.conf` (Ctrl-a prefix, mouse support, mobile-friendly)
- Auth: 1Password SSH agent (key-based)

## Troubleshooting

**"not a terminal" error:**
tmux needs a real terminal. Won't work from Claude Code's bash tool or scripts without a TTY.

**Can't connect via Tailscale:**
- Check `tailscale status` on both devices
- Ensure both are logged into same tailnet
- Try `tailscale ping <hostname>` from phone
