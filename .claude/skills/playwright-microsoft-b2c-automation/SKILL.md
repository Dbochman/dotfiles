---
name: playwright-microsoft-b2c-automation
description: >-
  Automate Microsoft or Azure AD B2C OIDC form_post logins with Playwright, including Enter-vs-click
  and persistent-session gotchas.
author: Claude Code
version: 1.0.0
date: 2026-05-24
---

# Playwright + Microsoft B2C Self-Heal Automation

## Problem

Automating a credential-based login against a Microsoft B2C tenant with Playwright is
deceptively hard. Each of these three gotchas independently makes a login flow appear
to work while silently failing somewhere in the chain. All three must be fixed
simultaneously for an end-to-end self-healing scraper to work.

## Context / Trigger Conditions

- The login redirects through a URL like
  `https://<tenant>.b2clogin.com/<tenant>.onmicrosoft.com/B2C_1A_<policy>/oauth2/v2.0/authorize?...&response_mode=form_post&...`
- The login form uses Knockout.js / Angular bindings (typical of B2C custom policies).
  Visible markers: input fields with ids `#signInName` and `#password`, submit button
  with id `#next` and text "Sign in".
- The backend uses ASP.NET Core / ASP.NET OWIN OIDC middleware (almost universal for
  B2C). Auth state cookie is named `.AspNetCore.Cookies` (or `.AspNet.Cookies`).
- Symptoms from any of the three gotchas:
  - Click on `#next` succeeds silently, page never navigates away from `/authorize`.
  - `page.url` reports `b2clogin.com/.../authorize` long after the screenshot and
    `page.title()` show the authenticated landing page.
  - Persistent context (`launch_persistent_context(user_data_dir)`) loses the auth
    state on `context.close()`; only Correlation/Nonce cookies persist to disk.

## Solution

Three independent fixes, all required:

### 1. Submit via Enter keypress, not click

Knockout/Angular form bindings don't reliably react to Playwright's programmatic
`.click()` on the submit button — the click is delivered at the CDP layer but the
framework's value bindings don't update, so the form submits empty (or doesn't submit
at all). Pressing Enter on the password field fires a real keyboard event that the
bindings honor.

```python
page.locator("#signInName").first.fill(username)
page.locator("#password").first.fill(password)
page.locator("#password").first.press("Enter")  # not .click("#next")
```

If the form is two-step (username page → password page on next), still use
`.press("Enter")` on each step. The submit-button approach can be left as a fallback
for edge cases, but Enter should be tried first.

### 2. Save `context.storage_state(path=...)` after successful auth

ASP.NET Core's auth cookie (`.AspNetCore.Cookies`) is a **session cookie** — it has no
expiry, so Chromium does NOT write it to the persistent user-data-dir's `Cookies`
SQLite. It lives only in browser memory. When `context.close()` runs, the auth state
vanishes. The next Playwright run from the same persistent dir is unauthenticated.

`context.storage_state(path=...)` explicitly serializes ALL cookies (session-scoped
and persistent) plus `localStorage` and `sessionStorage` to a JSON file. The scraper
loads it via `new_context(storage_state=path)` instead of reopening the persistent
dir.

```python
# Re-auth phase
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(SESSION_DIR, headless=True)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    # ... login flow that lands on the authenticated portal ...
    if authenticated:
        ctx.storage_state(path="storage_state.json")  # crucial
    ctx.close()

# Subsequent scrape phase
with sync_playwright() as p:
    if os.path.exists("storage_state.json"):
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state="storage_state.json")
    else:
        ctx = p.chromium.launch_persistent_context(SESSION_DIR, headless=True)
```

The persistent dir remains useful for the first-ever interactive login (when the user
runs the scraper non-headless to log in by hand); the storage_state file is what the
automated re-auth produces and what subsequent runs load.

### 3. Fall back to `page.title()` for auth detection — `page.url` can lag

Through an OIDC `response_mode=form_post` redirect chain, Playwright's `page.url` can
return the FIRST URL of the chain for tens of seconds (or indefinitely in some runs)
after the chain has visibly completed. The chain looks like:

```
b2clogin.com/.../authorize
  → b2clogin.com/.../CombinedSigninAndSignup/confirmed
  → <portal>/signin-oidc
  → <portal>/home
  → <portal>/account/...
```

`page.title()` and `page.content()` update correctly at each step; `page.url` doesn't
always. Any `is_authenticated()` predicate that relies solely on `page.url` will
return False forever even when the page is clearly logged in.

Robust pattern:

```python
def is_on_authenticated_page(page):
    url = page.url.lower()
    if "<your-portal-domain>" in url and "login" not in url and "b2clogin" not in url:
        if "<auth-path-marker>" in url:  # e.g. "account", "dashboard"
            return True
    # Fallback: page.url is unreliable through OIDC form_post; check title
    try:
        title = page.title()
        if "<authenticated page title marker>" in title:
            return True
    except Exception:
        pass
    return False
```

## Verification

End-to-end: a re-auth followed by a normal scrape, with no human in between, should
succeed against a portal that previously required manual cookie refreshes.

1. Wipe the session directory entirely: `rm -rf .scraper_session/`.
2. Run re-auth with credentials supplied via env: it should produce
   `storage_state.json` with cookies for `<tenant>.b2clogin.com`, the portal domain,
   and any API subdomains (e.g. `css-api.bwsc.org`).
3. Inspect:
   ```bash
   python3 -c 'import json; d=json.load(open("storage_state.json")); \
     print("cookies:", len(d["cookies"]), "domains:", sorted({c["domain"] for c in d["cookies"]}))'
   ```
   You should see > 10 cookies across all involved domains, not just the b2clogin ones.
4. Run the normal scrape with `--headless` and `storage_state=path`. It should
   complete API calls and return data, without ever hitting the interactive
   login-required exit.

## Example

Verified working pattern from `scrape_bwsc.py` (Boston Water and Sewer Commission,
behind `umaxcustomerportalprod.b2clogin.com`):

```python
def reauth_main(headless=True):
    username = os.environ["SCRAPER_USER"]
    password = os.environ["SCRAPER_PW"]
    os.makedirs(SESSION_DIR, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(SESSION_DIR, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)
        page.locator('#signInName, input[type="email"]').first.fill(username)
        page.locator('#password, input[type="password"]').first.fill(password)
        page.locator('#password, input[type="password"]').first.press("Enter")  # fix #1

        # Poll for auth; check both URL and title (fix #3)
        deadline = time.time() + 120
        while time.time() < deadline:
            time.sleep(1)
            if is_on_authenticated_page(page):  # has title fallback
                ctx.storage_state(path=STORAGE_STATE_PATH)  # fix #2
                ctx.close()
                return 0
        ctx.close()
        return 1
```

## Notes

- The `b2c_1a_..._signup_signin_mfa` suffix on the B2C policy name does NOT
  necessarily mean MFA is required on every login — many tenants name the policy
  this way even when MFA only kicks in on suspicious sessions. If the policy
  consistently demands an SMS or app code on first-step login, none of these three
  fixes help; you need a code source (Gmail filter / iMessage forwarding) and a
  fourth step that fills the code field.
- For React-rendered forms (OpenTable, etc.) the click-doesn't-fire problem also
  exists but the fix is different — see the `pinchtab-react-click-fix` skill, which
  uses a full `dispatchEvent` sequence with real `clientX/clientY` coordinates. For
  Microsoft B2C specifically, `.press("Enter")` on the password field is simpler and
  works.
- The persistent user-data-dir + `storage_state.json` combo is the right pattern even
  when sessions don't matter: storage_state is git-safe (single small JSON file),
  durable, and trivial to inspect. The persistent dir is megabytes of Chromium
  internals.
- Service Workers on the portal domain can briefly serve cached auth'd content even
  on a logged-out page, which compounds the `page.url` confusion. Using a fresh
  empty session dir for the re-auth phase removes that variable.

## References

- Playwright `BrowserContext.storage_state()`: https://playwright.dev/python/docs/api/class-browsercontext#browser-context-storage-state
- Microsoft B2C OIDC response modes: https://learn.microsoft.com/en-us/azure/active-directory-b2c/openid-connect
- ASP.NET Core cookie auth defaults (session-scoped): https://learn.microsoft.com/en-us/aspnet/core/security/authentication/cookie
- Related: `pinchtab-react-click-fix` (full dispatchEvent for React forms — different
  fix for similar symptom)
