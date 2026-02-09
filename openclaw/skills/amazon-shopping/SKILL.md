---
name: amazon-shopping
description: Search, browse, and purchase products on Amazon. Use when asked to buy something, find a product on Amazon, check prices, compare products, track orders, or manage the Amazon cart. Requires browser control and cached payment details.
allowed-tools: browser(*), Bash(amazon:*)
metadata: {"openclaw":{"emoji":"📦","requires":{"services":["browser"]}}}
---

# Amazon Shopping

Search, browse, and purchase products on Amazon using browser automation. Payment details are cached locally from 1Password.

## Important: Purchase Approval Required

**NEVER place an order without explicit approval from Dylan.**

Before clicking "Place your order":
1. Summarize: item name, price, shipping speed, delivery estimate
2. Wait for Dylan's explicit "yes" / "go ahead" / approval
3. If no response, do NOT proceed

## Spending Limits

```bash
# Read the spending cap before any purchase
cat ~/.cache/openclaw-gateway/visa_credit_limit
```

- **Hard cap**: Never exceed the credit limit (currently $250)
- **Soft cap**: Flag anything over $100 for extra confirmation
- When displaying prices, always include tax estimate if visible

## Shopping Flow

### 1. Search for Products

```
browser open "https://www.amazon.com"
browser snapshot
browser type <search_box_ref> "search query"
browser press Enter
browser snapshot
```

### 2. Browse Results

```
browser snapshot
# Click on a product to view details
browser click <product_ref>
browser snapshot
```

When presenting results to Dylan, include:
- Product name
- Price (and per-unit price if applicable)
- Star rating and review count
- Prime eligibility
- Delivery estimate

### 3. Add to Cart

```
browser click <add_to_cart_ref>
browser snapshot
```

### 4. Proceed to Checkout

```
browser open "https://www.amazon.com/gp/cart/view.cgi"
browser snapshot
browser click <proceed_to_checkout_ref>
browser snapshot
```

### 5. Fill Payment Details (if needed)

Amazon should have saved payment methods. If it asks for card details:

```bash
# Read cached card details
cat ~/.cache/openclaw-gateway/visa_number
cat ~/.cache/openclaw-gateway/visa_expiry
cat ~/.cache/openclaw-gateway/visa_cvv
cat ~/.cache/openclaw-gateway/visa_cardholder
```

Then fill the form fields:
```
browser type <card_number_ref> <number>
browser type <expiry_ref> <expiry>
browser type <cvv_ref> <cvv>
browser type <name_ref> <name>
```

### 6. Fill Shipping Address (if needed)

Read the local-only address file for field values:
```bash
cat ~/.openclaw/skills/amazon-shopping/address.local.md
```

If the file doesn't exist, fall back to:
```bash
cat ~/.cache/openclaw-gateway/visa_billing_address
```

### 7. Review Order (MANDATORY)

Before placing the order, **always**:

```
browser snapshot
```

Report to Dylan:
- Item(s) and quantities
- Item price(s)
- Shipping method and cost
- Tax
- **Order total**
- Estimated delivery date

**Then ask: "Should I place this order for $X.XX?"**

### 8. Place Order (ONLY after approval)

```
browser click <place_order_ref>
browser snapshot
```

Confirm the order was placed and report:
- Order confirmation number
- Expected delivery date

## Other Operations

### Check Order Status
```
browser open "https://www.amazon.com/gp/your-account/order-history"
browser snapshot
```

### View Cart
```
browser open "https://www.amazon.com/gp/cart/view.cgi"
browser snapshot
```

### Remove from Cart
```
browser open "https://www.amazon.com/gp/cart/view.cgi"
browser snapshot
browser click <delete_ref>
```

### Check Today's Deals
```
browser open "https://www.amazon.com/deals"
browser snapshot
```

### Track a Package
```
browser open "https://www.amazon.com/gp/your-account/order-history"
browser snapshot
browser click <track_package_ref>
browser snapshot
```

## Tips

- **Prime**: Dylan's account may have Prime — prefer Prime-eligible items for free shipping
- **Compare**: When asked to "find" something, show 2-3 options at different price points
- **Reviews**: Mention review count and rating — avoid items below 4 stars unless specifically requested
- **Subscribe & Save**: Mention if available for recurring purchases, but don't auto-enroll
- **Used/Renewed**: Only suggest if Dylan asks for cheaper options
- **Gift cards**: Never purchase gift cards without explicit request

## Safety Rules

- NEVER place an order without Dylan's explicit approval
- NEVER exceed the credit limit
- NEVER purchase gift cards, subscriptions, or recurring items without explicit request
- NEVER change account settings (address book, payment methods, Prime membership)
- NEVER share card details in messages — mask as `****0298`
- If CAPTCHA or 2FA appears, notify Dylan
- If the session expires (login required), notify Dylan — see Re-Auth Procedure below

## Re-Auth Procedure

The OpenClaw browser is a **separate Playwright Chromium instance** with its own profile at `~/.openclaw/browser/openclaw/user-data/`. It does NOT share cookies with regular Chrome. When Amazon asks to sign in:

1. **Stop the OpenClaw gateway** (it holds the browser profile lock):
   ```bash
   launchctl bootout gui/$(id -u)/ai.openclaw.gateway
   sleep 2
   pkill -f 'Google Chrome for Testing'
   rm -f ~/.openclaw/browser/openclaw/user-data/SingletonLock
   ```

2. **Launch visible Chromium** with the OpenClaw profile (must run in GUI session via `.command` file):
   ```bash
   # /tmp/amazon_login.js
   const { chromium } = require("/opt/homebrew/lib/node_modules/openclaw/node_modules/playwright");
   (async () => {
     const browser = await chromium.launchPersistentContext(
       "/Users/dbochman/.openclaw/browser/openclaw/user-data",
       { headless: false }
     );
     const page = browser.pages()[0] || await browser.newPage();
     await page.goto("https://www.amazon.com");
     await new Promise(r => setTimeout(r, 300000));
     await browser.close();
   })();
   ```
   Launch via: `open /tmp/amazon_login.command` (wrapper that runs node with the above script)

3. **Dylan signs in** on the Mac Mini screen (email, password, 2FA)

4. **Close browser and restart gateway**:
   ```bash
   pkill -f 'Google Chrome for Testing'
   rm -f ~/.openclaw/browser/openclaw/user-data/SingletonLock
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.gateway.plist
   ```

## Notes

- Browser is Playwright Chromium (NOT regular Chrome) — separate cookie store
- Browser profile: `~/.openclaw/browser/openclaw/user-data/`
- Playwright location: `/opt/homebrew/lib/node_modules/openclaw/node_modules/playwright`
- Must stop gateway before launching visible browser (SingletonLock conflict)
- Use `browser snapshot` frequently to stay oriented on the page
- Amazon's DOM changes frequently — always snapshot before interacting
- If a page looks wrong or unexpected, take a `browser screenshot` for visual confirmation
