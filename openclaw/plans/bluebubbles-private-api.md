# Plan: Enable BlueBubbles Private API

**Status**: Pending — requires physical access to Mac Mini at cabin
**Created**: 2026-02-28
**Prerequisite**: Be physically present at the Mac Mini (no remote workaround exists)

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

### 2. Shut down the Mac Mini

```bash
sudo shutdown -h now
```

Do NOT restart — you must fully shut down to enter Recovery Mode on Apple Silicon.

### 3. Boot into Recovery Mode

- Hold the **power button** until you see "Loading startup options"
- Click **Options** > **Continue**
- Enter password if prompted

### 4. Disable SIP

In the Recovery Mode terminal (Utilities > Terminal):

```bash
csrutil disable
```

Then restart from the Apple menu.

### 5. Enable Private API in BlueBubbles

After the Mac Mini reboots and the gateway comes back up:

1. Open **BlueBubbles** app (Screen Sharing or physically)
2. Go to the **Private API** section in settings
3. Toggle the **Private API switch ON**
4. Click the **refresh button** in the Private API Status box
5. It should show **"Connected"**

### 6. Verify

```bash
curl -s 'http://localhost:1234/api/v1/server/info?password=<pw>' | python3 -m json.tool
```

Confirm:
- `"private_api": true`
- `"helper_connected": true`

### 7. Test reactions

Send a test message and have OpenClaw react to it. The gateway log should show successful tapback delivery instead of the current "Private API is not enabled" error.

## Post-completion

- Update `accepted-risks.md` in the openclaw-operator repo: SIP disabled on Mac Mini (tradeoff: can't run iOS apps)
- The BlueBubbles DYLIB notification errors will stop appearing
- OpenClaw typing indicators will start working automatically

## Notes

- There is **no way to disable SIP remotely** — Apple requires physical presence (holding power button) on Apple Silicon
- Disabling SIP removes the ability to run iOS apps on the Mac Mini
- The Mac Mini gateway will restart automatically via LaunchAgent after reboot
