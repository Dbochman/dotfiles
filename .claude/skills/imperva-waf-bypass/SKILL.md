---
name: imperva-waf-python-requests
description: |
  Fix for Python requests returning 500 "Internal Server Error" when calling APIs
  protected by Imperva/Incapsula WAF, while the same request works with curl.
  Use when: (1) API call works with curl but fails with Python requests,
  (2) response headers contain "X-CDN: Imperva" or Incapsula cookies,
  (3) API returns 500 with no JSON body, just "Internal Server Error" plain text,
  (4) response contains Set-Cookie with visid_incap_ or nlbi_ prefixes.
  Solves bot detection issues with Imperva-protected APIs like Resy, and other
  services using Imperva/Incapsula CDN.
author: Claude Code
version: 1.0.0
date: 2026-02-07
---

# Imperva WAF Blocking Python Requests

## Problem
Python `requests` library calls to an API return `500 Internal Server Error` with
a plain text body, while the identical request via `curl` succeeds with 200. The
API is behind Imperva/Incapsula WAF which performs bot detection based on request
headers and TLS fingerprinting.

## Context / Trigger Conditions
- API call works with `curl` but returns 500 with Python `requests`
- Response headers include `X-CDN: Imperva`
- Response cookies contain `visid_incap_`, `nlbi_`, or `incap_ses_` prefixes
- Response body is plain text "Internal Server Error" (not JSON)
- The API itself is fine — it's the WAF/CDN layer rejecting the request

## Solution

Add browser-like headers to your requests session:

```python
headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://example.com",        # The web app's origin
    "Referer": "https://example.com/",       # The web app's URL
}
```

The critical headers are:
1. **User-Agent** — Must look like a real browser. Python requests sends `python-requests/2.x` by default which Imperva blocks.
2. **Accept** — Should match what a browser sends for API calls.

`Origin` and `Referer` may also be required depending on the API's CORS/WAF rules.

## Verification
- Response status changes from 500 to 200
- Response body contains actual JSON data instead of "Internal Server Error"
- `X-CDN: Imperva` header is still present (confirms you're going through the same path)

## Example

**Before (blocked):**
```python
resp = requests.post("https://api.resy.com/3/auth/password",
    headers={"Authorization": 'ResyAPI api_key="..."'},
    data={"email": "user@example.com", "password": "pass"},
)
# resp.status_code == 500
# resp.text == "Internal Server Error"
```

**After (works):**
```python
resp = requests.post("https://api.resy.com/3/auth/password",
    headers={
        "Authorization": 'ResyAPI api_key="..."',
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://resy.com",
        "Referer": "https://resy.com/",
    },
    data={"email": "user@example.com", "password": "pass"},
)
# resp.status_code == 200
# resp.json() == {"token": "...", ...}
```

## Notes
- The 500 is returned by Imperva's edge, not the origin server — that's why there's no JSON error body
- `curl` works because it sends a different TLS fingerprint and default headers that Imperva doesn't flag
- Some Imperva-protected APIs may also do JavaScript challenges for browser verification — this header approach won't work for those
- The User-Agent string should be periodically updated to match current Chrome versions
- This also applies to other WAFs (Cloudflare, Akamai) but the detection signals differ
- On older Python/LibreSSL setups, TLS fingerprinting may also be a factor — headers alone usually suffice for Imperva though

## References
- Discovered during Resy API integration (Feb 2026) — Resy uses Imperva CDN
- Imperva identifies bots via User-Agent, TLS fingerprint, and header ordering
