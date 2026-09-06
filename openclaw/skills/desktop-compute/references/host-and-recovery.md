# Desktop host and recovery

## Expected host

- Windows host: `DESKTOP-R9JS0OK`
- Compute environment: Ubuntu WSL, user `openclaw`
- GPU: NVIDIA RTX 5090 with 32 GB VRAM
- Transport: desktop-initiated reverse SSH over Tailscale to the Mac mini's
  loopback port 22022
- Restricted alias: `desktop-jobs`
- Trusted maintenance alias: `desktop-compute`

The restricted alias uses a separate key with a forced server command. A raw
command sent with that key should be rejected. Do not weaken that boundary to
recover a job.

## Healthy status

`desktop-compute status` should report `ok: true`, the `openclaw` user, the
expected GPU, sufficient disk space, and the requested tools. The WSL baseline
includes `rg`, `jq`, `unzip`, `zip`, `sqlite3`, `ffmpeg`, `shellcheck`, `git`,
`rsync`, `tmux`, Python, and user-local `uv`.

## Recovery order

1. Retry `desktop-compute status` once to rule out a brief reverse-tunnel
   transition.
2. From trusted maintenance, test `ssh desktop-compute 'systemctl is-active
   ssh; pgrep -af autossh'` without exposing keys or environment values.
3. Verify Tailscale is online on the desktop and Mini. Key expiry should remain
   disabled for this stationary trusted host.
4. Run the root-owned WSL bridge helper if the desktop is already reachable by
   an attended channel.
5. If the bridge fails only after a reboot, inspect the Windows scheduled task
   documented in `docs/ssh-host-access.md`. Do not open a public or LAN SSH
   firewall rule as a shortcut.

Do not restart Tailscale, WSL, the desktop, or a running tmux job merely because
one status probe timed out. Confirm the failed layer first.
