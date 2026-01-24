# tmux + Mobile Setup

Quick reference for accessing your Mac remotely via tmux + Tailscale + Termius.

## Connection Info

| Field | Value |
|-------|-------|
| Tailscale IP | `100.94.69.122` |
| Tailscale hostname | `dylans-mac` |
| Username | `dylanbochman` |
| Local IP (same WiFi only) | `192.168.165.106` |

## Termius Config

Create a host with:
- Hostname: `100.94.69.122` (works from anywhere)
- Username: `dylanbochman`
- Auth: SSH key (recommended) or password

## tmux Quick Reference

### Aliases (in ~/.zshrc)

```bash
t    # tmux
tn   # tmux new -s <name>
ta   # tmux attach -t <name>
tl   # tmux list-sessions
tk   # tmux kill-session -t <name>
td   # tmux detach
```

### Key Bindings (prefix is Ctrl-a)

| Action | Keys |
|--------|------|
| Detach | `Ctrl-a d` |
| Split vertical | `Ctrl-a |` |
| Split horizontal | `Ctrl-a -` |
| Switch panes | `Alt + arrow` (no prefix) |
| Kill pane | `Ctrl-a k` |
| Kill window | `Ctrl-a K` |
| Reload config | `Ctrl-a r` |
| Scroll mode | Mouse scroll or `Ctrl-a [` |

### Common Workflows

**Start a session before leaving desk:**
```bash
tn work
# do stuff, or leave something running
# Ctrl-a d to detach
```

**Reconnect from phone:**
```bash
ta work
```

**Check what's running:**
```bash
tl
```

## Config Location

- tmux config: `~/.tmux.conf`
- Shell aliases: `~/.zshrc` (search for "tmux aliases")

## Troubleshooting

**"not a terminal" error:**
tmux needs a real terminal. Won't work from Claude Code's bash tool or scripts without a TTY.

**Can't connect via Tailscale:**
- Check `tailscale status` on both devices
- Ensure both are logged into same tailnet
- Try `tailscale ping dylans-mac` from phone

**Session not found:**
- `tl` to list available sessions
- Session names are case-sensitive

## Related

- [tmux cheat sheet](https://tmuxcheatsheet.com/)
- [Tailscale docs](https://tailscale.com/kb)
