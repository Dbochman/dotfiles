---
name: grocery-reorder
description: Reorder groceries from Star Market by replaying the most recent order. Adds items to cart but does NOT checkout. Use when asked to reorder groceries, restock the kitchen, or place a Star Market order.
allowed-tools: Bash(pinchtab:*),Bash(grocery:*),Bash(bash:*)
metadata: {"openclaw":{"emoji":"G","requires":{"bins":["pinchtab","gws"]}}}
---

# Star Market Grocery Reorder

Automates weekly grocery reordering from Star Market (Albertsons family) using Pinchtab browser automation + CSMS API. Logs in as Julia, finds the most recent order, clicks Reorder to add all items to the cart, then verifies. Does NOT checkout.

## Usage

```bash
# Reorder most recent order (default)
python3 ~/.openclaw/workspace/scripts/grocery-reorder.py

# Reorder a specific order
python3 ~/.openclaw/workspace/scripts/grocery-reorder.py --order-id 160816581

# Dry run (login + list orders, no reorder)
python3 ~/.openclaw/workspace/scripts/grocery-reorder.py --dry-run
```

## Environment

Requires `STARMARKET_PASSWORD` in the environment. On the Mini this is loaded from `~/.openclaw/.secrets-cache`.

```bash
set -a && source ~/.openclaw/.secrets-cache && set +a
PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/usr/bin:/bin:$PATH
python3 ~/.openclaw/workspace/scripts/grocery-reorder.py
```

## Output

```json
{"status": "success", "order_id": "160816581", "cart": {"items": 9, "total": "34.00"}}
{"status": "dry-run", "order_id": "160816581", "orders": [...]}
```

## How It Works

1. **Login**: Navigates to Star Market sign-in page. If already logged in (Julia Joy visible), skips login. Otherwise:
   - CSMS API auth (`/abs/pub/cnc/csmsservice/api/csms/authn`) with device token
   - Password verify via API
   - MFA via email if needed: sends code via API, reads from Julia's Gmail via `gws`, verifies via API
   - Establishes browser session via `SWY.OKTA.autoSignInWithSessionToken(sessionToken)`
2. **Get Orders**: Navigates to `/order-account/orders`, extracts order IDs from `a[href*="/order-account/orders/"]` links
3. **Reorder**: Navigates to order detail page, clicks Reorder button via full Angular-compatible event dispatch (PointerEvent + MouseEvent sequence with real coordinates)
4. **Verify**: Navigates to `/erums/cart`, confirms items were added and reads cart total

## Key Technical Details

- **Angular event dispatch**: Standard `.click()` doesn't work on Angular buttons. Must dispatch full event sequence: pointerdown, mousedown, pointerup, mouseup, click (both Pointer and Mouse) with real `clientX`/`clientY` from `getBoundingClientRect()`
- **CSMS API headers**: Requires `Content-Type: application/vnd.safeway.v2+json`, `ocp-apim-subscription-key`, `x-swy-banner: starmarket`, `x-aci-user-hash`
- **XHR from browser**: API calls are made via XMLHttpRequest from within the browser (same-origin, inherits cookies)
- **Device remembered**: After first MFA, the device token is remembered and subsequent logins skip MFA
- **Cart URL**: `/erums/cart` (not `/shop/cart`)

## Cron Job

Weekly reorder runs on Sunday mornings. The cron job:
1. Runs the script
2. Reports results via iMessage to Dylan
3. Does NOT checkout — Julia reviews the cart and completes pickup scheduling

## Safety

- **NEVER checks out** — only adds items to cart
- Requires `STARMARKET_PASSWORD` environment variable
- MFA codes read from Julia's Gmail (requires `gws` auth for `julia.joy.jennings@gmail.com`)
- Pinchtab must be running with a Chrome profile that has Star Market cookies

## Troubleshooting

- **"No orders found"**: The Angular SPA may need more time to load. The script retries 6 times with 5-second intervals.
- **"SWY.OKTA not available"**: Pinchtab isn't on a Star Market page. Navigate to starmarket.com first.
- **MFA keeps being required**: The device token may have been invalidated. After one successful MFA, it should be remembered.
- **Reorder button "not found"**: The order detail page may have a different button label. Check the page text for the exact wording.
