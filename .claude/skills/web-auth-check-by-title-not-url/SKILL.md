---
name: web-auth-check-by-title-not-url
description: |
  Detect whether a browser automation page (Playwright, Puppeteer, Selenium) is
  on an authenticated view vs a login form when the portal uses "smart" URLs
  that serve different content based on session state. Use when: (1) building
  or debugging an is_authenticated() function for a scraper / self-heal flow,
  (2) is_authenticated returns False despite the page clearly showing the
  authenticated dashboard (account name, balance, etc.), (3) the URL contains
  a substring like "signin" / "login" / "auth" but the rendered page is post-
  authentication, (4) credential entry + MFA submission succeeds visibly but
  the scraper reports "kicked back to sign-in" and falls into a retry loop
  that burns MFA emails or causes account lockouts, (5) you're writing a check
  that combines positive URL paths AND a negative "not 'login' in url" guard
  and noticing it sometimes misfires. Covers the "smart landing URL" pattern
  used by Bank of America (/myaccounts/signin/signIn.go serves dashboard when
  authenticated, login form when not), and similar patterns at many consumer
  banking, brokerage, insurance, and government portals. Solves the
  "is_authenticated says False, retry loop runs, account gets fraud-flagged"
  failure mode that's invisible in screenshots (looks like a failed login)
  but is actually a misclassification of a SUCCESSFUL login.
author: Claude Code
version: 1.0.0
date: 2026-05-24
---

# Web Auth Check: Title Over URL

## Problem

Many consumer-facing portals — particularly banking, brokerage, and SiteMinder-
protected enterprise sites — use the same URL path to serve **two completely
different pages** depending on session state:

- Unauthenticated GET → login form
- Authenticated GET → dashboard / account view

Bank of America is the canonical example: `https://secure.bankofamerica.com/myaccounts/signin/signIn.go` renders the login form when the request has no session cookies, and renders the authenticated "Accounts Overview" dashboard when it does. Same URL string. Different rendered DOM. Different `page.title()`.

A `is_authenticated(page)` check that uses URL substring matching ("`signin`
not in url", "URL contains `/myaccounts/`", etc.) gets this WRONG. It will
report False when the user is actually logged in, triggering retry loops,
re-MFA submissions, MFA-email floods, and bank-side fraud-flag accumulation.
Worst case, this loop fires for hours of debugging while the underlying
login was working the entire time.

## Context / Trigger Conditions

Your is_authenticated logic is hitting this trap when:

- Scraper logs say "kicked back to sign-in" but a screenshot at the same
  moment shows the user's account / dashboard.
- A re-auth flow visibly completes (credentials accepted, MFA code accepted)
  but the scraper's auth check returns False and retries.
- `page.url` and `page.title()` disagree: URL contains a sign-in / login
  string, title contains an account-view phrase ("Accounts Overview",
  "Dashboard", account-holder name, etc.).
- You added a "negative URL guard" like `"signin" not in url` AND a positive
  "URL contains `/myaccounts/`" check, and the page is matching both
  positive AND negative at once (the substring is in BOTH halves of the URL).

## Solution

**Use `page.title()` as the primary signal. Fall back to URL only when title
is empty or ambiguous.**

```python
def is_authenticated(page):
    # 1. Try title first — most reliable disambiguator for "smart" URLs
    try:
        title = page.title() or ""
    except Exception:
        title = ""

    # 1a. Negative title markers (definitely the login form, regardless of URL)
    LOGIN_TITLE_MARKERS = ("Log In", "Login", "Sign In", "Sign-In",
                            "User ID", "Welcome Back", "Enter Password")
    if any(m in title for m in LOGIN_TITLE_MARKERS):
        return False

    # 1b. Positive title markers (authenticated landing)
    AUTH_TITLE_MARKERS = ("Accounts Overview", "My Accounts", "Dashboard",
                          "Account Summary", "Account Details", "Mortgage",
                          "Welcome,",  # often "Welcome, <user-name>"
                          "Portfolio")
    if any(m in title for m in AUTH_TITLE_MARKERS):
        return True

    # 2. URL fallback — only for paths that exist ONLY when authenticated.
    # Do NOT use negative URL guards like "signin" not in url — they misfire
    # on smart landing URLs.
    url = page.url.lower()
    if any(p in url for p in ("/dashboard", "/portfolio", "/transactions",
                              "/billing", "/auth/security-center/")):
        return True

    return False
```

**Key rules:**

1. **Title check ALWAYS wins.** If the title clearly says authenticated, you
   are authenticated. If it clearly says login form, you are not. URL
   substring conflicts are noise.
2. **Negative title markers are reliable.** "Log In" / "Sign In" / "User ID"
   in `<title>` is the bank EXPLICITLY telling you you're on the login form,
   regardless of URL.
3. **Negative URL guards are unreliable** for smart-landing portals. The same
   word ("signin") can appear in both unauth and auth URLs.
4. **Positive URL paths can be useful but secondary.** `/dashboard` /
   `/portfolio` paths are usually unambiguous — they only exist when
   authenticated. But always title-check first.
5. **Customize markers per portal** during initial discovery. Run a one-off
   inspector that hits the portal both pre-auth and post-auth, dumps the
   title of each, and copy the exact substrings into your check.

## Verification

For each portal you build a scraper against, **explicitly verify the smart-URL
assumption** with two inspector runs:

```python
# Run 1: not authenticated (fresh session dir)
print("PRE-LOGIN:", page.url, "—", page.title())

# Run 2: post manual login (same URL!)
print("POST-LOGIN:", page.url, "—", page.title())
```

If URLs are identical and titles differ, you've confirmed the smart-URL
pattern and your is_authenticated MUST use titles.

If URLs differ post-auth (most portals do this), the URL check works fine
and the title-first approach is just defense in depth.

## Example

Verified-working pattern from `scrape_mortgage.py` against
`bankofamerica.com`:

```python
def is_authenticated(page, lender):
    if lender == "boa":
        if "bankofamerica.com" not in page.url.lower():
            return False
        try:
            title = page.title() or ""
        except Exception:
            title = ""
        # Negative: title clearly says login form
        if "Log In" in title or "User ID" in title:
            return False
        # Positive: title matches authenticated landings
        if any(s in title for s in ("Accounts Overview", "My Accounts",
                                     "Mortgage", "Account Summary",
                                     "Security Center", "Account Details")):
            return True
        # URL fallback: paths that ONLY exist post-auth
        url = page.url.lower()
        if any(p in url for p in ("/auth/security-center/", "/mortgage/")) \
                and "/auth/signon/" not in url:
            return True
        return False
```

Before this fix, `is_authenticated` rejected the URL
`https://secure.bankofamerica.com/myaccounts/signin/signIn.go` even though
that URL's title was "Bank of America | Online Banking | Accounts Overview"
(the authenticated dashboard). The misclassification caused ~10 unnecessary
MFA email submissions over an afternoon, getting close to a fraud-detection
lockout, before a CDP page-inspector dump revealed the actual title.

## Notes

- **Banks with smart URLs are common.** Verified in this pattern: Bank of
  America. Likely similar pattern (untested): Chase
  (`/banking/SignIn.aspx`), Wells Fargo (`/das/cgi-bin/session.cgi`), Capital
  One. Always inspect title pre- vs post-auth as a first step.
- **Non-banking sites do this too.** Many Salesforce-powered customer
  portals reuse a single `/s/login` URL pre and post auth. Microsoft B2C
  policies sometimes do.
- **`page.title()` can briefly be empty or stale during navigation.** Add
  a short `time.sleep(1)` or `wait_for("networkidle")` before the title
  check when timing matters.
- **Title can also lie**, just less often. Some sites set a generic title
  ("Welcome") that doesn't disambiguate. Combine title + a known authed-
  only DOM element (e.g. `page.locator("[data-testid='user-menu']").count() > 0`)
  for highest reliability.
- **Don't bake URL strings into is_authenticated**. The URL conventions
  CHANGE — banks rebrand login paths every few years. Title content tends
  to be more stable (the brand wants "Accounts Overview" in the title for
  SEO / UX consistency).
- Related: [[playwright-device-trust-bootstrap]] (where this insight enables
  bank-grade automation by correctly detecting post-MFA success);
  [[playwright-email-mfa-flow]] (where the title fallback is also documented
  for OIDC URL-lag in a different failure mode).

## References

- Playwright `page.title()`: https://playwright.dev/python/docs/api/class-page#page-title
- Playwright `locator.wait_for()`: https://playwright.dev/python/docs/api/class-locator#locator-wait-for
