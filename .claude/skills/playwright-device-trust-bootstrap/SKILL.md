---
name: playwright-device-trust-bootstrap
description: |
  Bootstrap a Playwright persistent-context session so that "remember this
  device" / device-trust cookies issued by banking, brokerage, insurance, and
  other MFA-protected portals (Bank of America, Chase, Wells Fargo, Fidelity,
  Schwab, etc.) carry over into subsequent headless re-auth runs and skip MFA
  on every login. Use when: (1) setting up a new Playwright scraper against a
  portal that prompts "Yes, remember this device" during MFA and you want
  subsequent automated logins to skip MFA, (2) a scraper's --re-auth flow
  works (correct credentials, MFA code typed and submitted, post-MFA dashboard
  visible) but EVERY automated re-run still hits the full MFA flow as if no
  device-trust cookie was issued, (3) someone is about to "log in manually
  first to establish device trust" via their everyday Chrome/Safari and you
  need to redirect them to the right bootstrap method, (4) the headless host
  is a remote server (Mac mini, Linux VPS) and the user is on a different
  machine. Solves the non-obvious pitfall that device-trust cookies issued
  during a manual browser login do NOT transfer to a Playwright persistent
  context running on a different machine or in a different browser profile —
  they must be captured INSIDE the exact persistent context the headless
  re-auth will later use.
author: Claude Code
version: 1.0.0
date: 2026-05-24
---

# Playwright Device-Trust Bootstrap

## Problem

Banking and other high-security portals issue a "device-trust cookie" when a
user ticks "Yes, remember this device" during MFA. The cookie tells the
backend to skip MFA on subsequent logins from the SAME browser. For automated
scrapers using Playwright, this is the difference between needing
human-supplied MFA codes on every run vs. a fully unattended self-heal flow.

The non-obvious failure mode: users intuitively try to "set up device trust"
by logging in manually via their everyday Chrome / Safari / Firefox. **This
does not work.** The trust cookie lands in *that* browser's profile — not in
the Playwright `launch_persistent_context(SESSION_DIR)` directory the headless
re-auth uses. Future automated runs from that SESSION_DIR see no trust cookie
and hit the full MFA flow every time.

## Context / Trigger Conditions

You're hitting this skill's problem if:

- You're building or fixing a Playwright re-auth flow against a portal that
  shows a "remember this device" / "trust this device" / "don't ask again"
  checkbox during MFA.
- A user manually logged in via their everyday browser (or a separate Chrome
  profile) thinking it would help. The headless re-auth still hits MFA on
  every run.
- The headless scraper runs on a server (Mac mini, Linux VPS, Docker
  container) while the user is on a different machine.
- `--re-auth --headless` successfully completes the MFA flow once but
  subsequent runs always hit MFA again — the trust cookie isn't being
  captured.
- You're tempted to write a "trust-cookie sync" script that copies cookies
  from system Chrome to `.scraper_session/`. Stop — that path is fragile (the
  trust cookie is often tied to a device fingerprint that includes user agent
  + canvas hash + WebGL hash + TLS fingerprint, which differs between system
  Chrome and Playwright Chromium).

## Solution

**The manual bootstrap must happen inside the exact persistent context the
headless re-auth uses.** Specifically:

1. Confirm where the scraper's persistent context lives — usually
   `<repo>/.<lender>_session/`, set via
   `launch_persistent_context(SESSION_DIR, headless=...)`.
2. Get to a Terminal **on the host that runs the headless scraper** — not the
   user's laptop. Tailscale Screen Sharing → SSH → Terminal works. macOS:
   `open vnc://<host>` from a trusted client.
3. Make sure the host has a GUI session you can interact with (this is the
   one step that doesn't work over plain SSH on a headless server — you need
   either VNC / Screen Sharing OR you need to use Xvfb / wayvncpipe and drive
   the keyboard remotely, which is rare). On a Mac mini in someone's home,
   Screen Sharing over Tailscale is the right answer.
4. Run the scraper WITHOUT `--headless` from the Terminal:
   ```bash
   cd <repo>
   ./venv/bin/python3 scrape_<lender>.py --lender <name>      # mortgage-style
   # or for single-lender scrapers:
   ./venv/bin/python3 scrape_<name>.py                        # no --headless flag
   ```
5. A visible Chromium window opens, pointed at `SESSION_DIR`. Wait at the
   login page until you see "Waiting for you to log in to <provider>..." or
   similar in the Terminal.
6. Log in interactively: type credentials, satisfy MFA. **Crucially, tick
   "Yes, remember this device" / "Trust this device" / equivalent before
   clicking Submit on the MFA prompt.**
7. Let the scraper run to completion OR (if your scraper has a `--re-auth`
   that saves `storage_state.json`) cancel after auth and run `--re-auth`
   from the same context. Either way, the persistent context now contains
   the device-trust cookie.

Test the bootstrap worked:
```bash
./venv/bin/python3 scrape_<lender>.py --lender <name> --re-auth --headless
```
The log should show navigation through login but NO MFA-related steps — the
scraper goes straight from password submission to the post-login dashboard.
If you still see the MFA page (`/users/mfa`, `/authcode`, etc.), the trust
cookie didn't land — re-do the bootstrap and double-check you ticked
"remember this device".

## Verification

Three signals confirm device-trust capture succeeded:

1. **Cookie presence**: inspect the persistent context's cookies for a
   trust-shaped cookie. Names vary by vendor — search for cookies with names
   containing `trusted`, `device`, `recognize`, `dnt`, `mfa_trust`, or `2fa`.
   Long expiry (30-365 days) is typical.
   ```bash
   sqlite3 .lender_session/Default/Cookies \
     "SELECT host_key, name, datetime(expires_utc/1000000-11644473600,'unixepoch') AS expires
      FROM cookies WHERE name LIKE '%trust%' OR name LIKE '%device%' OR name LIKE '%recognize%';"
   ```
2. **Headless re-auth log**: the next `--re-auth --headless` run does NOT
   log MFA-page-related events.
3. **Production behavior**: the next scheduled scrape run (after enough time
   for any in-memory state to expire) succeeds without alerting you for
   manual intervention.

## Example

Verified-working flow from `scrape_mortgage.py` against `bankofamerica.com`
(2026-05-24):

1. On Dylan's MBP, Screen Share into `dylans-mac-mini` via Tailscale.
2. On the Mini's Terminal:
   ```bash
   cd ~/repos/financial-dashboard
   rm -rf .boa_session   # start fresh
   ./venv/bin/python3 scrape_mortgage.py --lender boa
   ```
3. A Chromium window opens on the Mini's display, navigates to
   `https://secure.bankofamerica.com/myaccounts/signin/signIn.go`.
4. Manually enter username + password, click Log In.
5. BoA prompts for an authorization code; click "Get code a different way"
   if email is preferred. Type the code that lands in Gmail.
6. On the same MFA page, tick **"Yes, remember this device"** before
   clicking Submit.
7. BoA may park the session at
   `secure.bankofamerica.com/auth/security-center/additional-security-features/`
   for additional device registration — complete whatever it asks (or
   navigate to `/myaccounts/` directly).
8. Scraper detects `is_authenticated()` and proceeds to its normal scrape.
9. Persistent context now has BoA's `dvcid` / `recognize` / similar trust
   cookie.

Subsequent test: `./venv/bin/python3 scrape_mortgage.py --lender boa
--re-auth --headless` skips the MFA flow entirely — BoA recognizes the
trust cookie and lets the headless scraper through directly to the dashboard.

## When this skill is NOT enough (verified ceiling)

The persistent-context bootstrap solves "cookies don't carry over." It does
NOT solve "the bank detects headless and refuses to honor cookies they DID
carry over." Some portals (Bank of America, almost certainly Chase /
Wells Fargo, possibly some brokerage/insurance) actively fingerprint the
browser at login time and treat any "headless-looking" session as untrusted
regardless of what cookies are present. Symptoms:

- Manual bootstrap inside the correct SESSION_DIR completes successfully —
  user typed MFA, ticked "remember this device", reached the dashboard.
- Subsequent headless `--re-auth` from the SAME SESSION_DIR hits the full
  MFA flow on EVERY run. Cookies are present in `.session_dir/Default/Cookies`
  with long expiries, but the bank ignores them.
- Subsequent headless `--headless` SCRAPE (no `--re-auth`) reports "session
  expired" on the same SESSION_DIR that worked non-headless minutes ago.

**These basic stealth flags are individually insufficient against bank-grade
detection (Bank of America verified, 2026-05):**

| Stealth measure | What it hides | Defeats BoA? |
|---|---|---|
| `channel="chrome"` | Chromium-fork user-agent + binary differences | No |
| `ignore_default_args=["--enable-automation"]` | `navigator.webdriver === true` | No |
| `add_init_script` to override `navigator.webdriver` | Same as above, defense-in-depth | No |
| `args=["--headless=new"]` | Old shell-headless render-path tells | No |
| ALL FOUR COMBINED | (all of the above) | **STILL No** for BoA |

Banks at this tier also check window/screen dimension ratios, hardware
concurrency, missing input devices (no mouse movement), canvas + WebGL
rendering quirks, JS-engine timing patterns, and TLS/JA3 fingerprints —
many of which Playwright headless trips regardless of CLI flags.

If you hit this ceiling, the realistic options are:

1. **CDP attach to a Chrome NOT launched by Playwright (PROVEN to work
   against Bank of America, 2026-05).** When Playwright is the launcher,
   it injects automation infrastructure at process startup — and BoA-tier
   detection catches this regardless of flags. When Playwright merely
   ATTACHES via `connect_over_cdp(...)` to a Chrome that something else
   (Pinchtab, a manually-started Chrome with `--remote-debugging-port`,
   etc.) already launched, the Chrome process has no automation tells, and
   the bank treats the session as a normal user. **This is the highest-
   leverage workable approach.** See "Working pattern: CDP attach" below.
2. **Accept manual re-bootstrap** as the operating model. Tier the scraper
   as "alert-only" — the cron alerts when the session expires (every few
   weeks/months), the human does the bootstrap dance once. Lowest effort,
   no fingerprint arms race. Reasonable fallback if option 1's
   infrastructure isn't already in place.
3. **`playwright-stealth` / `playwright-extra-plugin-stealth`** patches
   dozens of fingerprint vectors at once. Higher chance of working than
   raw Playwright but a real dependency to maintain and may itself be
   detected as fingerprints evolve.
4. **Run headed Chrome inside Xvfb** (Linux) or a virtual display
   equivalent on macOS. Slow, requires display server setup. Usually
   inferior to option 1 since CDP attach is simpler and more reliable.

## Working pattern: CDP attach to externally-launched Chrome

Verified end-to-end against BoA on 2026-05-24, after 10+ failed Playwright-
launch attempts. The pattern:

**Setup (one-time per host):**
1. Install a Chrome manager that runs Chrome with a stable
   `--remote-debugging-port=N` and persistent `--user-data-dir=...`.
   We used Pinchtab (`brew install pinchtab` or via npm; `pinchtab daemon
   install`, then set `instanceDefaults.mode = headed` so Chrome runs
   visibly on the host's GUI for the bootstrap step).
2. Verify Chrome is up with a discoverable CDP port:
   ```bash
   ps auxww | grep -oE 'remote-debugging-port=\d+'
   # → remote-debugging-port=9869
   curl -sS http://127.0.0.1:9869/json/version  # should return Chrome info
   ```

**Bootstrap (one-time per bank-session-expiry, weeks-to-months apart):**
3. Screen Share into the host, drive the Chrome tab manually to the bank,
   log in, MFA, tick "remember this device". The session and any device-
   trust cookie land in Chrome's `--user-data-dir`.

**Scrape (every cron run, hands-off):**
4. From the scraper, discover the CDP port at runtime (don't hardcode):
   ```python
   import re, subprocess
   def find_cdp_url(user_data_dir_marker):
       ps = subprocess.run(["ps", "auxww"], capture_output=True,
                           text=True, timeout=5).stdout
       for line in ps.split("\n"):
           if user_data_dir_marker in line:
               m = re.search(r"--remote-debugging-port=(\d+)", line)
               if m: return f"http://127.0.0.1:{m.group(1)}"
       return None
   ```
5. Attach via Playwright and use the **existing** page:
   ```python
   with sync_playwright() as p:
       cdp_url = find_cdp_url(".pinchtab/profiles")  # or your marker
       browser = p.chromium.connect_over_cdp(cdp_url)
       context = browser.contexts[0]
       page = context.pages[0]
       # ... scrape via page.evaluate / page.locator / etc ...
       browser.close()  # disconnect — does NOT kill the Chrome process
   ```

**Critical lifecycle rules:**

- **DO NOT** call `page.goto()` to "go to the portal." The existing tab is
  the user's authenticated session; navigating elsewhere can invalidate
  the session. Just use the page wherever it is.
- **DO NOT** call `context.close()`. It's Chrome's default context — closing
  would terminate the Chrome manager's open tabs.
- `browser.close()` on a CDP-attached browser is JUST a Playwright-side
  disconnect; the Chrome process keeps running.
- Use `context.pages[0]` — the existing tab — not `context.new_page()`.
- For `is_authenticated`, use [[web-auth-check-by-title-not-url]] —
  smart-landing URLs will lie if you check URL substrings.

**Detection / alerting:**

- When the bank-side session expires (weeks/months), the next scrape's
  `is_authenticated` returns False. The scraper should exit with a clear
  message — the cron alerts the human, who re-bootstraps once.
- Bootstrap is so infrequent that this is genuinely "set and forget" for
  3+ weeks at a time.

For most personal-finance use cases against bank-grade detection, the CDP-
attach pattern is the right answer. The engineering is ~50-100 lines (CDP
URL discovery + connect_over_cdp + lifecycle handling) plus a few-times-
per-year manual bootstrap.

## Notes

- **Device-trust scope varies by vendor.** Most banks scope by browser
  cookies (the pattern documented here). A minority bind trust to a TLS /
  TCP / device fingerprint that's invisible to cookies — for those, even
  bootstrapping inside the right SESSION_DIR may not help, and you'll need
  to keep going through MFA on every run.
- **Trust expires.** Even after successful bootstrap, expect to redo this
  every 30-365 days as the trust cookie expires. The scraper should detect
  this (an MFA prompt suddenly appears on what was a working headless run)
  and either re-trigger the MFA flow automatically OR alert a human to
  re-bootstrap.
- **The bootstrap is one-time per session-dir + lender.** If you ever
  delete `.lender_session/` (or it gets corrupted), you have to bootstrap
  again. Consider checking the session dir into a private backup if losing
  it would require a major bootstrap effort.
- **DON'T** try to bootstrap by:
  - Logging in via your everyday Chrome/Safari (cookies won't transfer).
  - Copying cookies from system Chrome's Cookies SQLite into `.session_dir/`
    (encryption keys differ; even with same keys, fingerprint binding
    usually still fails).
  - Using `chrome_profile_dir` to point Playwright at the system Chrome
    profile (only works if Chrome is fully closed AND the user is fine
    with Playwright modifying their everyday profile — usually a non-starter).
- **Headless servers with no GUI** need an alternative: install Xvfb (Linux)
  or use a non-headless Playwright run inside a VNC server (macOS:
  built-in Screen Sharing). For one-off bootstrap, manual via VNC is the
  simplest and least error-prone path.
- Related: [[playwright-email-mfa-flow]] (complementary — automates the MFA
  step once device-trust is bootstrapped, OR runs every time when device-
  trust isn't possible or hasn't been bootstrapped yet);
  [[playwright-microsoft-b2c-automation]] (B2C-specific MFA gotchas).

## References

- Playwright `launch_persistent_context`: https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context
- Chromium cookie encryption (macOS): https://chromium.googlesource.com/chromium/src/+/refs/heads/main/docs/security/cookie_encryption.md
- Tailscale Screen Sharing: https://tailscale.com/kb/1080/cli (for macOS, use `vnc://<hostname>` in Safari)
