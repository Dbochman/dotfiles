---
name: playwright-email-mfa-flow
description: |
  Complete email-OTP / email-MFA self-heal flow for Playwright scrapers against
  banking, mortgage, insurance, utility, and SaaS portals that email a one-time
  code on every login. Use when: (1) building or fixing automated re-auth for a
  portal that sends a numeric/alphanumeric code via email (e.g. PennyMac,
  Wells Fargo, Chase, Fidelity, insurance carriers, Workday HR portals),
  (2) Playwright `fill()` on the OTP field appears to work but the verify
  button stays disabled and the form shows "This field is required" or
  similar, (3) the OTP submit button is `type="button"` (not "submit") with a
  JS onclick handler and pressing Enter on the OTP input does nothing,
  (4) `.click()` on the verify button times out even though the button is
  visibly enabled, (5) you're checking `page.url` for the post-MFA dashboard
  URL and it stays stuck on `/users/mfa` or `/2fa` for 30+ seconds while
  page.title() and page.content() clearly show the authenticated dashboard.
  Covers the full pattern: timestamp-anchored Gmail polling via gws CLI,
  Knockout-friendly keystroke typing, JS-click fallback for the verify button,
  title-based auth detection for OIDC URL lag, and the digit-prefix gotcha
  where portals show static prefixes (e.g. "PM-") as labels next to the
  input rather than as part of the expected value.
author: Claude Code
version: 1.0.0
date: 2026-05-24
---

# Playwright Email-MFA Self-Heal Flow

## Problem

Automating a Playwright login against a portal that emails a one-time code on
every login looks deceptively simple — fetch the email, type the code, click
verify — but five distinct gotchas conspire to make a naive implementation
fail silently. Each was bisected through end-to-end iteration against a real
banking portal; missing any one of them breaks the flow.

## Context / Trigger Conditions

Your login flow is hitting this pattern's failure modes if:

- You can successfully fill username + password and reach a page like
  `/users/mfa`, `/2fa`, `/verify`, `/otp`, or one whose title contains
  "Verification" / "Two-Step Authentication".
- The page tells you "we just sent a code to your email" and an input field
  is waiting for it.
- After `page.fill()` of the code, the form-validation error message (e.g.
  "This field is required. If you did not receive the email, please check
  your spam folder.") **stays visible** and the Verify button stays disabled.
- Pressing Enter on the code field does nothing because there's no real form
  to submit — the button is `<input type="button">` or `<button type="button">`
  with a JS click handler.
- `page.locator(verify_btn_selector).click()` times out at 5-10s with
  `Locator.click: Timeout exceeded` even though the button is visibly enabled
  in screenshots.
- Polling `page.url` for the post-MFA dashboard URL waits indefinitely while
  `page.title()` actually updates correctly.

## Solution

A six-step recipe. **All six are required** — partial implementations fail in
ways that look like "MFA broken" when really one specific step is the issue.

### 1. Timestamp BEFORE submitting the login form

```python
before_ms = int(time.time() * 1000)
# ... now submit username + password and wait for the MFA page ...
```

The Gmail fetch in step 2 will use `after:<unix_seconds>` and filter on
`internalDate > before_ms`. Without this anchor, a re-run of the scraper picks
up a stale code from a previous attempt — wasting an MFA send and submitting
an expired code.

### 2. Poll Gmail via `gws` CLI for the MFA email

```python
import base64, json, re, subprocess, time

def fetch_mfa_code_from_gmail(account, sender, subject_substring, code_pattern,
                               before_ms, timeout_s=120, poll_interval_s=5):
    after_s = int(before_ms / 1000)
    query = f'from:{sender} subject:"{subject_substring}" after:{after_s}'
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        listed = subprocess.run(
            ["gws", "gmail", "users", "messages", "list", "--account", account,
             "--params", json.dumps({"userId": "me", "q": query, "maxResults": 5}),
             "--format", "json"],
            capture_output=True, text=True, timeout=30,
        )
        if listed.returncode == 0:
            for msg in json.loads(listed.stdout).get("messages", []):
                got = subprocess.run(
                    ["gws", "gmail", "users", "messages", "get", "--account", account,
                     "--params", json.dumps({"userId": "me", "id": msg["id"], "format": "full"}),
                     "--format", "json"],
                    capture_output=True, text=True, timeout=30,
                )
                if got.returncode != 0:
                    continue
                full = json.loads(got.stdout)
                if int(full.get("internalDate", "0")) <= before_ms:
                    continue  # double-check: must be strictly newer
                # Walk parts to find text/plain body
                body = _extract_text_body(full.get("payload", {}))
                if body:
                    m = code_pattern.search(body)
                    if m:
                        return m.group(1)
        time.sleep(poll_interval_s)
    return None
```

The MFA email typically lands within 3-6 seconds of login submission. Don't
start polling until you've confirmed the page reached the MFA URL — earlier
polls just burn API calls.

### 3. Handle prefix-label gotcha when typing the code

Many portals display a **static prefix as a label next to the input field**
rather than as part of the expected value. PennyMac shows `PM-` as text in
the form layout; the user-facing email contains `PM-4576251` but the field
expects only `4576251`. Typing `PM-4576251` leaves a "This field is required"
error because the value doesn't pass the digits-only validation.

Try the part after `-` first, then the full code as fallback:

```python
candidates = []
if "-" in code:
    candidates.append(code.split("-", 1)[1])  # digits-only
candidates.append(code)  # full code
```

### 4. Type with delay, not fill(), for Knockout-bound inputs

`page.locator(...).fill(value)` updates the DOM value but does NOT always
fire the input events that Knockout (and some Angular) viewModels listen to.
Symptom: the field VISUALLY contains the code, but the bound viewModel still
thinks it's empty, the validation error stays, and the Verify button stays
disabled.

Workaround: focus the input, then `page.keyboard.type` with a per-character
delay so each `input` event fires naturally. Press `Tab` after to fire
`change` / `blur`.

```python
inp = page.locator(code_input_selector).first
inp.click()  # focus
page.keyboard.press("ControlOrMeta+a")  # select existing text
page.keyboard.press("Delete")           # clear it
page.keyboard.type(candidate, delay=50)
page.keyboard.press("Tab")              # fires blur/change
time.sleep(1)                           # let validation re-run
```

(This is related to but DIFFERENT FROM the React click problem covered in
the existing `pinchtab-react-click-fix` skill. For Knockout, full
`dispatchEvent` sequences aren't needed — just the input events from real
keystrokes.)

### 5. Click the verify button; JS-click fallback

The verify button is usually `type="button"` (not "submit") with a JS onclick
handler — pressing Enter on the input doesn't submit, you must click. But
Playwright's `.click()` actionability check sometimes times out on
Knockout-toggled buttons that change enabled-state via DOM mutation rather
than the `disabled` attribute. Fall back to a direct JS `.click()` via
`page.evaluate`:

```python
try:
    page.locator(submit_selector).first.click(timeout=5000)
except Exception:
    btn_id = submit_selector.lstrip("#")
    page.evaluate(
        "id => { const el = document.getElementById(id); if (el) el.click(); }",
        btn_id,
    )
```

The JS click works because by this point Knockout has registered the typed
value (step 4 above) and the onclick handler is wired up — the only thing
that was failing was Playwright's actionability check.

### 6. Wait at least 45 seconds, check title not just URL

Post-MFA navigation through OIDC redirect chains can take 20-45 seconds on
banking/lender sites. AND `page.url` lags through these chains — it can stay
stuck on `/users/mfa` for tens of seconds after the page is rendering the
authenticated dashboard. Same gotcha as Microsoft B2C; see the
[[playwright-microsoft-b2c-automation]] skill.

Build the auth check with a title fallback:

```python
def is_authenticated(page, lender):
    url = page.url.lower()
    if expected_authed_domain in url and expected_authed_path_marker in url:
        return True
    # Fallback: page.url lags through OIDC, but page.title() updates
    try:
        title = page.title()
        if "Dashboard" in title or expected_post_login_title_marker in title:
            return True
    except Exception:
        pass
    return False
```

Poll for ~45 seconds:

```python
for _ in range(45):
    time.sleep(1)
    if is_authenticated(page, lender):
        context.storage_state(path=storage_state_path)
        return True
```

## Verification

End-to-end test (start from a clean session dir to mimic a real re-auth):

```bash
rm -rf .lender_session/
SCRAPER_USER=$(op read 'op://OpenClaw/<lender>/username') \
SCRAPER_PW=$(op read 'op://OpenClaw/<lender>/password') \
  ./venv/bin/python3 scrape_<lender>.py --re-auth --headless
```

Expected log progression:
```
[re-auth] navigating to https://<portal>/
[re-auth] post-navigate URL: https://<idp>/users/sign_in
[re-auth] filled username
[re-auth] filled password
[re-auth] pressed Enter on password field
[re-auth] poll start, URL: https://<idp>/users/mfa
[mfa] on MFA page — fetching code via Gmail
[mfa] got code 'XXX-1234567' from message <id> (received 4500ms after submit)
[mfa] attempt 1: submitted code '1234567' via click
[mfa] authenticated after attempt 1, URL: https://<portal>/
[re-auth] saved storage_state to .lender_session/storage_state.json
```

Then verify the persisted state actually works for the next scrape:
```bash
./venv/bin/python3 scrape_<lender>.py --headless --dry-run
```

Should print `Authenticated successfully.` and pull real data without
hitting the MFA page again — the trust-this-device cookie (if the portal
issues one) is now in `storage_state.json` and good for subsequent runs
until it expires.

## Example

Verified-working from `scrape_mortgage.py` against PennyMac
(`mypennymac.pennymac.com` / `identity.pennymac.com`):

```python
LENDERS = {
    "pennymac": {
        "op_item": "PennyMac",
        "login": {
            "url": "https://mypennymac.pennymac.com/",
            "username_selector": "#username",
            "password_selector": "#password",
            "submit_selector": "#submit-button",
            "submit_method": "enter",
        },
        "mfa": {
            "page_url_marker": "/users/mfa",
            "code_input_selector": "#tfaEmail",
            "code_submit_selector": "#login-tfa-email-verify-btn",
            "email_account": "<gmail-address-on-file-for-the-portal>",
            "email_sender": "NoReply@pennymac.com",
            "email_subject": "Email Confirmation",
            "code_regex": r"(PM-\d{7})",
        },
    },
}
```

After the fix: 9 monthly mortgage payments extracted on the very first
post-re-auth scrape run, without any further human interaction.

## Notes

- **Banking bot-detection is less aggressive than expected for portals that
  route consumer logins through OIDC.** PennyMac never showed a CAPTCHA or
  device-fingerprint challenge. The login succeeded headless from a brand-new
  session dir. This may not generalize to portals using Akamai BMP or
  PerimeterX (BoA likely; Wells Fargo definitely).
- **Track failed-attempt counts.** Banking portals typically lock accounts
  after 3-5 wrong-password attempts. The first attempt at this flow may burn
  one attempt on each iteration of testing. After more than 2 consecutive
  failures, STOP and have the user verify the 1P item by manually logging in
  with the same values before retrying.
- **Inspect MFA emails for the prefix pattern.** Always look at one real MFA
  email to determine the code format (`PM-\d{7}`, `\d{6}`, `[A-Z]{2}\d{4}`,
  etc.). Don't try to use a generic `\d{6}` regex — it'll false-match other
  numbers in the email body (account numbers, dates, etc.).
- The `gws` CLI is OAuth-scoped per Gmail account. The portal-registered
  email address determines which account to poll. For shared accounts
  (e.g. PennyMac registered to Julia's gmail, BoA registered to Dylan's),
  the `mfa.email_account` config differs per scraper.
- The MFA-email retention window matters: if the scraper runs more than once
  in 5 minutes (e.g. during a flaky CI loop), the second run might pick up
  the FIRST run's expired code if `internalDate > before_ms` check is missing.
- Related: [[playwright-microsoft-b2c-automation]] (URL-lag fallback pattern),
  [[playwright-post-login-interstitial]] (post-login nag screens), and
  [[pinchtab-react-click-fix]] (React-specific click event dispatch — different
  fix for similar visual symptom).

## References

- Playwright keyboard typing: https://playwright.dev/python/docs/api/class-keyboard#keyboard-type
- Playwright locator actionability: https://playwright.dev/python/docs/actionability
- Gmail API users.messages.list: https://developers.google.com/gmail/api/reference/rest/v1/users.messages/list
- Knockout.js value bindings (why fill doesn't always trigger updates): https://knockoutjs.com/documentation/value-binding.html
