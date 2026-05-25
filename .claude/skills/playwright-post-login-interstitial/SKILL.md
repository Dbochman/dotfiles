---
name: playwright-post-login-interstitial
description: |
  Diagnose Playwright re-auth / scraper login flows that appear to fail at the
  auth-check step even though credentials are correct and the form was submitted.
  Use when: (1) a Playwright login fills username + password, clicks submit (or
  presses Enter), and the form clearly submitted (network request fired, cookies
  changed) yet `page.url` stays on the login URL and `page.title()` stays on the
  login title, (2) `is_authenticated(page)` returns False indefinitely but
  `page.content()` / a screenshot of the page shows the post-login site
  navigation alongside a modal/dialog/interstitial like "Would you like to turn
  on two-step verification?", "Accept updated Terms of Service", "Verify your
  email address", "Welcome — take a tour", or "Your password expires in N days
  — update now?", (3) auth state cookies ARE present in the browser but the
  scraper still fails on the next request because the SPA is gated on
  dismissing the interstitial. Solves the "login worked but post-login nag
  screen blocks the scraper" problem common to utility-bill, banking, and SaaS
  portals (Eversource, ConEd, many bank portals, Workday-style HR portals).
author: Claude Code
version: 1.0.0
date: 2026-05-24
---

# Playwright Post-Login Interstitial Dismissal

## Problem

A Playwright re-auth flow submits credentials successfully, the server authenticates,
and the auth state cookies are in the browser — but the portal renders an "optional"
post-login interstitial (2FA enrollment nag, ToS update, email-verification reminder,
onboarding tour, password-rotation warning, etc.) that blocks the SPA from navigating
to the real authenticated landing page. Naïve `is_authenticated()` checks based on
`page.url` or `page.title()` return False forever, the re-auth loop times out, and
the next scrape run still sees "session expired" symptoms.

This is DISTINCT from the OIDC `page.url`-lag failure mode (see
[[playwright-microsoft-b2c-automation]]). There the URL has actually completed the
redirect chain and is just stale; here the URL is correctly reporting that we are
still on the login path because the SPA hasn't moved past the interstitial.

## Context / Trigger Conditions

You're hitting this skill's problem if all of these are true:

- A Playwright re-auth fills the username/password and submits — fill logs say "filled
  username", "filled password", "pressed Enter" or "clicked submit" with no errors.
- Cookies have changed: `len(context.cookies())` jumped, auth-related cookies appeared
  (e.g., `.AspNetCore.Cookies`, session tokens, JWT-bearing cookies).
- `page.url` stays on the login path (`/login`, `/security/account/login`, `/signin`).
- `page.title()` stays on the login title ("X | Log In", "Sign In - Y").
- BUT a screenshot or `page.content()` dump shows BOTH the post-login site navigation
  (Account & Billing menu, user name in header, etc.) AND an additional dialog/modal
  asking the user to do something optional.
- The interstitial has at least one "skip" button — phrasing varies: "Ask Me Again
  Later", "Not Now", "Skip", "Remind Me Later", "Continue Without", "I'll Do It Later",
  "Maybe Later".

## Solution

After the credential submission, before failing the auth check, look for the dismiss
button by accessible role/text, click it, and re-poll for the authenticated state.

```python
def dismiss_post_login_interstitial(page, dismiss_labels=None, timeout=20):
    """Click a "skip this nag screen" button if one is shown, then wait up to
    `timeout` seconds for is_authenticated to return True. Returns True if the
    page reached an authenticated state."""
    dismiss_labels = dismiss_labels or [
        "Ask Me Again Later",
        "Not Now",
        "Skip",
        "Remind Me Later",
        "Maybe Later",
        "I'll Do It Later",
        "Continue Without",
        "Decline",
    ]
    button = None
    for label in dismiss_labels:
        # Prefer accessible role match — works whether it's a <button> or an <a>
        candidate = page.get_by_role("button", name=label)
        if candidate.count() == 0:
            candidate = page.locator(f'button:has-text("{label}"), a:has-text("{label}")')
        if candidate.count() > 0:
            button = candidate.first
            print(f"  [interstitial] dismissing via {label!r}")
            break
    if button is None:
        return False
    button.click()
    for _ in range(timeout):
        time.sleep(1)
        if is_on_authenticated_page(page):
            return True
    return False
```

Wire it as a fallback in the re-auth main:

```python
success = reauth_with_creds(page, username, password, ...)
if not success:
    success = dismiss_post_login_interstitial(page)
if success:
    context.storage_state(path=STORAGE_STATE_PATH)
```

Order matters: only run the dismiss step AFTER the normal auth-check has had a chance
to succeed, so a flow that doesn't show an interstitial still works through the fast
path.

## Verification

1. Wipe the session directory: `rm -rf .scraper_session/`.
2. Run re-auth and confirm the log shows `[interstitial] dismissing via "<label>"`.
3. Confirm the next log line is `is_authenticated` succeeding with a URL that
   contains the post-login path (e.g. `/cg/customer/Account#/details/0` for
   Eversource).
4. Run the normal scrape (loading the saved `storage_state.json`). It should
   authenticate to the API/portal on the first request without re-prompting.

## Example

Verified-working example from `scrape_eversource.py` (Eversource utility portal,
custom Sitecore — NOT Microsoft B2C):

The Eversource login page submits successfully but the post-login flow renders a
"Would you like to turn on two-step verification?" interstitial with **Get Started**
and **Ask Me Again Later** buttons, even when the user has previously disabled SMS
2FA on their account. `page.url` stays at `/security/account/login`, `page.title()`
stays "Eversource | Log In", and `page.content()` includes the Account & Billing
nav alongside the interstitial.

The fix added to `reauth_main()`:

```python
success = reauth_with_creds(
    page, username, password,
    login_url="https://www.eversource.com/security/account/login",
    username_selector='input[name="email"]',
    password_selector='#password, input[name="password"]',
    submit_selector='#signIn',
    is_authenticated=is_on_authenticated_page,
    timeout=120,
)
if not success:
    success = _dismiss_2fa_prompt_and_recheck(page)  # looks for "Ask Me Again Later"
if success:
    context.storage_state(path=STORAGE_STATE_PATH)
```

After the fix, the log transitions:
```
[re-auth] timed out after 120s, final URL: https://www.eversource.com/security/account/login
[re-auth] dismissing 2FA-enrollment interstitial
[re-auth] authenticated after 2FA dismiss, URL: https://www.eversource.com/cg/customer/Account#/details/0
[re-auth] saved storage_state to .eversource_session/storage_state.json
```

Subsequent scrape pulls 25 bills (Opower API) without any further interaction.

## Notes

- **Always dump page content (or screenshot) at timeout BEFORE assuming the fill or
  click didn't work.** A bare timeout log can't distinguish "form never submitted"
  from "form submitted but post-login interstitial appeared" — and the fixes are
  completely different. One log line of `page.content()[:2000]` would have saved
  an hour during the Eversource debugging.
- The label-list approach (try several common dismiss phrasings) makes this robust
  across portals you haven't seen yet. Don't hardcode `"Ask Me Again Later"` — the
  next portal you scrape will use `"Not Now"` or `"Skip"`.
- Common interstitial patterns this covers:
  - 2FA / MFA enrollment nag (Eversource, many banks, several SaaS)
  - "Accept updated Terms of Service" / "Accept updated Privacy Policy"
  - "Verify your email address" / "Confirm your phone number"
  - "Take a tour" / "Welcome" onboarding modals
  - "Your password expires in N days — update now?" (forced rotation prompts)
  - "Set up account recovery" (recovery-email or backup-codes nag)
- If the portal aggressively re-prompts the interstitial on every login (some banks
  do this), consider running the dismiss step every time as the LAST step of re-auth,
  not just on timeout — promote it from fallback to standard.
- If a portal has a non-skippable interstitial (e.g., "you MUST update your password
  to continue"), no automation can solve it: surface a clear error and require a
  human to log in manually once.

## References

- Related: [[playwright-microsoft-b2c-automation]] — also covers Playwright login flows
  but for a different failure mode (OIDC `page.url` lag during the redirect chain).
- Playwright `Page.get_by_role()`: https://playwright.dev/python/docs/api/class-page#page-get-by-role
- Playwright text selectors / `has-text`: https://playwright.dev/python/docs/other-locators#css-locator
