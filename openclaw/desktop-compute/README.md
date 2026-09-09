# Desktop compute host boundary

This directory tracks the root-owned components that turn the private RTX 5090
desktop into a durable managed compute host.

| File | Installed location | Purpose |
|---|---|---|
| `openclaw-wsl-reverse-ssh` | WSL `/usr/local/sbin/` | Validates/starts sshd and maintains the reverse tunnel to the Mac mini |
| `openclaw-desktop-dispatch` | WSL `/usr/local/libexec/` | Forced-command protocol for the restricted autonomous-job key |
| `install-prelogin-bridge.ps1` | Attended elevated Windows PowerShell | Registers a least-privilege at-startup S4U task with bounded retries |

The Windows Startup-folder launcher remains as a recoverable fallback until a
cold-boot canary proves the scheduled task starts WSL and restores the Mini's
loopback listener before an interactive login. Both paths call the same
idempotent WSL helper, so a later Startup invocation exits harmlessly when the
tunnel already exists.

The installer is staged on the Windows Desktop at
`C:\Users\Owner\Desktop\Install-OpenClaw-Prelogin-Bridge.ps1`. Registering an
at-startup task requires one elevated run. The task itself runs as Owner with a
limited S4U token; elevation is used only to register the boot trigger.

From **PowerShell opened with Run as administrator**:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\Desktop\Install-OpenClaw-Prelogin-Bridge.ps1"
```

Keep the Startup fallback after registration. On the next convenient reboot,
verify the Mini's port 22022 listener before signing into Windows; only then is
it safe to retire the fallback.

The trusted `desktop-compute` SSH identity remains available for deliberate
maintenance. OpenClaw uses a different Mini-local identity through the
`desktop-jobs` alias. Its authorized-key entry is restricted and forces the
root-owned dispatcher; it cannot request an arbitrary shell or forwarding.

The dispatcher records immutable run ownership, dependencies, resource
reservations, and runner PIDs. Resource acquisition is lock-serialized, and an
unfinished run remains a holder even after its tmux session disappears. The
restricted cancellation operation only writes an owner-checked cooperative
request for a sealed workflow; interrupted-run recovery remains a deliberate
operator task after process inspection.
