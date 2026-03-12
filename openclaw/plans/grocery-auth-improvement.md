# Improving Grocery Reorder Auth with Sweet Cookie

## Problem

The Star Market grocery reorder skill (`openclaw/skills/grocery-reorder/`) currently goes through a full login flow every time:
1. Session check → CSMS API auth → password verify → MFA via Gmail (gws) → Okta sign-in
2. This is slow, fragile, and depends on MFA email parsing

## Proposed Solution

Use [sweet-cookie](https://github.com/steipete/sweet-cookie) (`@steipete/sweet-cookie`) to extract authenticated browser cookies and inject them into the reorder script, skipping the login flow entirely.

## How Sweet Cookie Works

- TypeScript/Node library (requires Node ≥22 — Mini has this)
- Reads browser cookie SQLite DBs directly
- Decrypts cookie values using OS keychain credentials
- Returns cookies as HTTP headers ready for injection

### Browser-Specific Details

| Browser | Cookie DB Path | Decryption | Keychain Access |
|---------|---------------|------------|-----------------|
| Chrome | `~/Library/Application Support/Google/Chrome/.../Cookies` | AES-128-CBC via PBKDF2 (`'saltysalt'`, 1003 iterations, SHA1) | `security find-generic-password -w -a "Chrome" -s "Chrome Safe Storage"` |
| Safari | `~/Library/Cookies/Cookies.binarycookies` | None (plaintext binary format) | Not needed |

## Launchd Compatibility (Mini)

| Step | Chrome | Safari |
|------|--------|--------|
| Read cookie DB | Needs FDA — OpenClawGateway already has it | Needs FDA — same, should work |
| Keychain decrypt | `security` CLI may hang under launchd (same class as `op read` hang) | Not needed |
| **Verdict** | Risky without workaround | **Clean path** |

## Options

### Option A: Safari (Recommended)

1. Log into Star Market once in Safari on the Mini
2. sweet-cookie reads `~/Library/Cookies/Cookies.binarycookies` — no keychain needed
3. Extract session cookies, inject into reorder script
4. Cookies refresh naturally as long as Safari session stays alive

**Pros:** No keychain hang risk, simplest path
**Cons:** Need to maintain a Safari session on Mini (no headless Safari)

### Option B: Chrome with Pre-cached Keychain Password

1. Manually run `security find-generic-password -w -a "Chrome" -s "Chrome Safe Storage"` on Mini
2. Save the result to `~/.openclaw/.secrets-cache` (e.g., `CHROME_SAFE_STORAGE_KEY=...`)
3. Patch sweet-cookie (or write a wrapper) to use the cached key instead of calling `security`
4. Cookie DB read works under launchd since gateway has FDA

**Pros:** Chrome is the primary browser, more likely to have active sessions
**Cons:** More work, key changes if Chrome re-encrypts (rare but possible)

### Option C: Pinchtab Token Capture (Current Approach Enhanced)

Keep the current Pinchtab login flow but cache the resulting session token for reuse across runs. Only re-auth when the token expires.

**Pros:** No new dependencies
**Cons:** Still need MFA on first auth, token may expire frequently

## Dependencies

- `@steipete/sweet-cookie` — npm package
- Node ≥22 (already on Mini at `/opt/homebrew/opt/node@22/bin`)
- FDA on the reading process (OpenClawGateway already has this)

## Next Steps

1. Install sweet-cookie on Mini: `npm install -g @steipete/sweet-cookie`
2. Test Safari cookie extraction manually: verify Star Market cookies are readable
3. If Safari works, update grocery-reorder skill to try cookie injection before falling back to full login
4. If Safari doesn't work (no active session), try Option B with Chrome

## References

- Sweet Cookie repo: https://github.com/steipete/sweet-cookie
- Current grocery skill: `openclaw/skills/grocery-reorder/SKILL.md`
- Current reorder script: `openclaw/workspace/scripts/grocery-reorder.py`
- Star Market uses CSMS API (Albertsons/Safeway family) — session cookies are standard HTTP cookies
