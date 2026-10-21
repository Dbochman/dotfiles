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
reservations, and runner PIDs. Resource acquisition is host-wide and
lock-serialized, inputs freeze when run provenance is written, and an
unfinished run remains a holder even after its tmux session disappears. The
restricted cancellation operation only writes an owner-checked cooperative
request for a sealed workflow; interrupted-run recovery remains a deliberate
operator task after process inspection.

## Windows eMudhra distrust mitigation

`block-emudhra-intermediates.ps1` adds the 16 known compromised intermediate
certificates (eight keys, direct and cross-signed certificates) to the current
Windows user's **Disallowed** store. It never imports trusted roots, changes
execution policy, or edits application bundles. Every DER certificate must
match a SHA-256 fingerprint pinned from Mozilla incident 2083449 before the
store is opened for mutation. The default invocation is read-only.

Supply the public inventory from
<https://emudhra.we-have-a-problem.com/impacted-intermediates.json>. The script
intentionally refuses a changed count, unknown fingerprint or duplicate;
review inventory changes rather than loosening validation.

From a checkout containing the reviewed script, in Windows PowerShell:

```powershell
# Read-only validation and proposed additions:
powershell -NoProfile -ExecutionPolicy Bypass -File .\block-emudhra-intermediates.ps1 -InventoryPath C:\audit\impacted-intermediates.json
# Explicit apply; the receipt's parent must exist and the file must be new:
powershell -NoProfile -ExecutionPolicy Bypass -File .\block-emudhra-intermediates.ps1 -InventoryPath C:\audit\impacted-intermediates.json -Apply -ReceiptPath C:\audit\distrust-receipt.json
```

The execution-policy override above is limited to that reviewed process. The
owner explicitly approved it for the October 20 audit; do not change the
persistent machine/user policy to run diagnostics. The receipt identifies
only added versus pre-existing public certificate entries. Reapplication is
idempotent and does not overwrite receipts. Do not remove known compromised
certificate distrust merely because a browser update arrives.

On the RTX 5090 desktop, both fresh Chrome and Edge audit profiles rejected all
12 TLS canaries and passed both controls after this mitigation; before it,
Chrome accepted six and Edge accepted twelve. Chromium documents consumption
of the Windows Current User Untrusted Certificates store in its
[root-store FAQ](https://chromium.googlesource.com/chromium/src/+/main/net/data/ssl/chrome_root_store/faq.md).
This is **current-user** protection, not clearance for other accounts,
independent Node/Python/Java stores, existing browser connections, or every
application's networking. Native Windows trust already rejected the canaries.

Run `openclaw/tests/test_windows_emudhra_distrust.ps1` on Windows with
`-ScriptPath`, `-InventoryPath`, and an existing `-TempDirectory` to check the
read-only path and six fail-closed input/receipt cases without trust changes.
Keep live TLS and fresh-browser tests separate from those script tests.

The deployed helper, verified public inventory, native diagnostic and receipts
are under `%LOCALAPPDATA%\OpenClaw\Security\emudhra-20261020`. Detailed evidence,
WSL repair, retirement recovery location, and remaining coverage limits are in
[dependency maintenance](../plans/dependency-maintenance.md#windows-desktop-follow-up--late-2026-10-20).
