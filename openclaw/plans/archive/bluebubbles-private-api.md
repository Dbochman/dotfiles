# Plan: Enable BlueBubbles Private API

**Status**: Complete
**Created**: 2026-02-28
**Completed**: 2026-03-02
**Prerequisite**: Someone physically at the Mac Mini with keyboard and mouse (or trackpad)

## Why

BlueBubbles Private API is disabled because macOS SIP (System Integrity Protection) is enabled. Without it, the following features don't work:

| Feature | Without Private API | With Private API |
|---------|-------------------|-----------------|
| Send/receive messages | Yes | Yes |
| Read receipts | Yes | Yes |
| Typing indicators | No | Yes |
| Reactions/tapbacks | No | Yes |
| Edit/unsend messages | No | Yes |
| iMessage effects | No | Yes |
| Reply threading | No | Yes |
| Group management | No | Yes |

## Steps

### 1. Disable Library Validation (already done 2026-02-28)

```bash
sudo defaults write /Library/Preferences/com.apple.security.libraryvalidation.plist DisableLibraryValidation -bool true
```

### 2. Person at cabin: Shut down the Mac Mini

Click Apple menu → Shut Down. Wait until completely off (no lights, no fan noise).

Do NOT restart — must fully shut down to enter Recovery Mode on Apple Silicon.

### 3. Person at cabin: Boot into Recovery Mode

- **Press and hold the power button** until you see "Loading startup options" (~10 seconds)
- Release the power button
- Click **Options** (gear icon), then click **Continue**
- Select your user if prompted, enter password if prompted

**Important**: This step requires a mouse/trackpad. The startup options picker is a graphical UI and keyboard-only navigation (Tab) is unreliable. A USB mouse is safest.

### 4. Person at cabin: Disable SIP

1. In the menu bar, click **Utilities** → **Terminal**
2. Type and press Return:
   ```bash
   csrutil disable
   ```
3. If prompted to confirm, type **y** and press Return
4. If prompted for password, enter the Mac Mini password
5. Reboot:
   ```bash
   reboot
   ```

### 5. Dylan (remote): Verify SIP is disabled

Wait ~2 minutes for normal boot, then:

```bash
ssh dylans-mac-mini "csrutil status"
```

Expected: `System Integrity Protection status: disabled.`

### 6. Dylan (remote): Enable Private API in BlueBubbles

Option A — via BlueBubbles GUI (Screen Sharing):
1. Open **BlueBubbles** app
2. Go to the **Private API** section in settings
3. Toggle the **Private API switch ON**
4. Click the **refresh button** in the Private API Status box
5. It should show **"Connected"**

Option B — check if BB API supports enabling it:
```bash
ssh dylans-mac-mini "curl -s 'http://localhost:1234/api/v1/server/info?password=\$BLUEBUBBLES_PASSWORD' | python3 -m json.tool"
```

### 7. Dylan (remote): Verify

```bash
ssh dylans-mac-mini "curl -s 'http://localhost:1234/api/v1/server/info?password=\$BLUEBUBBLES_PASSWORD' | python3 -m json.tool"
```

Confirm:
- `"private_api": true`
- `"helper_connected": true`

### 8. Dylan (remote): Test reactions

Send a test message and have OpenClaw react to it. The gateway log should show successful tapback delivery instead of the current "Private API is not enabled" error.

## Post-completion

- Update `accepted-risks.md` in the openclaw-operator repo: SIP disabled on Mac Mini (tradeoff: can't run iOS apps)
- Update `enforcement-map.md`: SIP status changed
- The BlueBubbles DYLIB notification errors will stop appearing
- OpenClaw typing indicators and reactions will start working automatically

## Notes

- **`nvram recovery-boot-mode=unused` does NOT work on Apple Silicon** — Apple blocks NVRAM boot-mode writes as part of "1TR" (One True Recovery) security. Tested 2026-03-01 on M4 Pro, returns `(iokit/common) not permitted`.
- The only way into Recovery Mode on Apple Silicon is physically holding the power button
- A **mouse/trackpad is required** for the startup options picker — Tab navigation is undocumented and unreliable
- Disabling SIP removes the ability to run iOS apps on the Mac Mini (not needed)
- All LaunchAgents (gateway, BB, watchdog, presence, cielo, etc.) restart automatically after reboot
