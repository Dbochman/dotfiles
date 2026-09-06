# Desktop host and toolchain

Load this reference only for host diagnosis or when preparing a new job script.

## Access boundary

- Restricted job alias: `desktop-jobs`, available from the OpenClaw Mac Mini
  only and forced through `/usr/local/libexec/openclaw-desktop-dispatch`
- Trusted maintenance alias: `desktop-compute`, outside this skill and reserved
  for deliberate operator administration
- Remote account: password-locked Ubuntu/WSL user `openclaw`, with no Linux
  `sudo` membership
- Remote job root:
  `/mnt/c/Users/Owner/Documents/OpenClaw/remote-splat/jobs/<job>/`
- Per-job directories: `input/`, `outputs/`, `logs/`, and `state/`
- Protected execution helpers:
  `/home/openclaw/.local/state/desktop-compute/splat/<job>/`
- Durable session: `splat-<job>`
- GPU probe: `/usr/lib/wsl/lib/nvidia-smi`

The desktop opens a reverse TCP tunnel to the Mini over Tailscale. WSL `sshd`
and the Mini listener are both loopback-only. Mosh cannot use this transport.
Do not add Windows firewall rules, Tailscale Serve, password authentication,
root SSH, sudo, or another listener from this skill.

Windows interop is enabled because COLMAP, Brush, and conversion currently run
as Windows applications. WSL therefore is not a hostile-user security boundary:
an approved job script can launch Windows processes and can re-enter the distro
through `wsl.exe` as root. The autonomous key cannot open an arbitrary shell;
it can only stage fixed-root files and launch an exact hash-approved script
through the root-owned dispatcher. Both SSH identities must remain only on the
Mini.

## Verified hardware and software

Verified September 6, 2026:

- NVIDIA GeForce RTX 5090, 32,607 MiB, driver 610.88
- Ubuntu 26.04 under WSL 2
- `tmux` 3.6a, `rsync` 3.4.1, Git 2.53, plus the shared WSL utility baseline
  (`rg`, `jq`, archives, SQLite, FFmpeg, ShellCheck, and user-local `uv`)
- Windows-native COLMAP 4.2.0 at
  `/mnt/c/Users/Owner/Documents/Codex/2026-09-05/c/work/colmap/bin/colmap.exe`
- Windows-native Brush 0.3.0 at
  `/mnt/c/Users/Owner/Documents/Codex/2026-09-05/c/work/brush/brush_app.exe`
- `splat-transform` under
  `/mnt/c/Users/Owner/Documents/Codex/2026-09-05/c/work/conversion/node_modules/.bin/`

Those application paths are the currently verified bootstrap toolchain, not
the durable storage contract. Discover and validate a replacement path before
editing a job script if that dated workspace moves. Keep datasets and new
outputs in the managed job root.

After a transport or toolchain repair, the bundled
`scripts/compute_canary.sh` may be staged, hash-approved, and run as an ordinary
job. It is read-only and verifies the expected GPU plus all three application
paths without starting reconstruction or training.

Windows executables can be launched from WSL and Windows paths can be produced
with `wslpath -w`. Prefer script-relative inputs and outputs. Do not assume a
native WSL COLMAP, Brush, Node, CUDA toolkit, or Python environment is installed.
