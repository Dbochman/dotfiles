#!/usr/bin/env python3
"""
Star Market grocery reorder automation via pinchtab + CSMS API.

Flow:
1. API login (auth + password verify) from within pinchtab browser
2. MFA via email if needed (send code API -> read from Gmail -> verify API)
3. Establish browser session via SWY.OKTA.autoSignInWithSessionToken()
4. Navigate to orders page, find most recent order
5. Navigate to order detail, click Reorder via JS event dispatch
6. Verify items were added to cart

Usage: python3 grocery-reorder.py [--order-id ORDER_ID] [--dry-run]
"""
import json
import subprocess
import time
import re
import base64
import os
import sys
import argparse

# ============================================================
# Configuration
# ============================================================
PT_BASE = "http://127.0.0.1:9867"
GWS = "/opt/homebrew/bin/gws"
JULIA_EMAIL = "julia.joy.jennings@gmail.com"
SM_USER = os.environ.get("STARMARKET_USERNAME", "juliajoyjennings@gmail.com")
SM_PASS = os.environ.get("STARMARKET_PASSWORD", "")
SUB_KEY = "9e38e3f1d32a4279a49a264e0831ea46"
USER_HASH = "29fb95f3f8a30a41fb17ac7e74543649e5a69d54e423d2ba66a91d67a28918bb"
DEVICE_TOKEN = "0bbf660dcdd2bed236a204f4eaa54bb6"
SM_BASE = "https://www.starmarket.com"

# ============================================================
# Helpers
# ============================================================
def pt_eval(js):
    """Evaluate JavaScript in pinchtab browser."""
    payload = json.dumps({"expression": js})
    r = subprocess.run(
        ["curl", "-s", "-m", "30", "-X", "POST", f"{PT_BASE}/evaluate",
         "-H", "Content-Type: application/json", "-d", payload],
        capture_output=True, text=True
    )
    if not r.stdout:
        return ""
    d = json.loads(r.stdout)
    return d.get("result", d.get("error", ""))

def pt_nav(url):
    """Navigate pinchtab to a URL."""
    subprocess.run(
        ["curl", "-s", "-m", "30", "-X", "POST", f"{PT_BASE}/navigate",
         "-H", "Content-Type: application/json",
         "-d", json.dumps({"url": url})],
        capture_output=True, text=True
    )

def browser_xhr(path, body_dict, result_var="__r"):
    """Make an XHR call from within the browser (inherits cookies/session)."""
    body_json = json.dumps(body_dict)
    pt_eval(f"window.__{result_var}_body = " + json.dumps(body_json))
    time.sleep(0.3)
    xhr_js = f'''(function(){{
  window.{result_var} = "pending";
  var xhr = new XMLHttpRequest();
  xhr.open("POST", "{path}", true);
  xhr.setRequestHeader("Content-Type", "application/vnd.safeway.v2+json");
  xhr.setRequestHeader("Accept", "application/vnd.safeway.v2+json");
  xhr.setRequestHeader("ocp-apim-subscription-key", "{SUB_KEY}");
  xhr.setRequestHeader("x-swy-banner", "starmarket");
  xhr.setRequestHeader("x-swy-client-id", "web-portal");
  xhr.setRequestHeader("x-swy-correlation-id", "c-" + Date.now());
  xhr.setRequestHeader("x-swy-date", new Date().toUTCString());
  xhr.setRequestHeader("x-aci-user-hash", "{USER_HASH}");
  xhr.withCredentials = true;
  xhr.onload = function(){{ window.{result_var} = xhr.responseText; }};
  xhr.onerror = function(){{ window.{result_var} = "ERROR:" + xhr.status; }};
  xhr.send(window.__{result_var}_body);
  return "started";
}})()'''
    pt_eval(xhr_js)
    for _ in range(20):
        time.sleep(1)
        r = pt_eval(f"window.{result_var}")
        if r and r != "pending":
            try:
                return json.loads(r)
            except:
                return {"_raw": r[:500]}
    return {"_error": "timeout"}

def js_click(selector_js):
    """Click a button found by JS selector using full event dispatch (Angular-compatible)."""
    return pt_eval(f'''(function(){{
  var target = {selector_js};
  if(!target) return "not found";
  var rect = target.getBoundingClientRect();
  var x = rect.x + rect.width/2;
  var y = rect.y + rect.height/2;
  var opts = {{bubbles:true, cancelable:true, clientX:x, clientY:y, button:0, view:window}};
  target.dispatchEvent(new PointerEvent("pointerdown", opts));
  target.dispatchEvent(new MouseEvent("mousedown", opts));
  target.dispatchEvent(new PointerEvent("pointerup", opts));
  target.dispatchEvent(new MouseEvent("mouseup", opts));
  target.dispatchEvent(new PointerEvent("click", opts));
  target.dispatchEvent(new MouseEvent("click", opts));
  return "clicked: " + target.textContent.trim().slice(0, 50);
}})()''')

def get_verification_code(min_time=0):
    """Fetch the latest Albertsons verification code from Julia's Gmail."""
    env = {**os.environ, "PATH": "/opt/homebrew/bin:/usr/bin:/bin"}
    r = subprocess.run(
        [GWS, "gmail", "users", "messages", "list",
         "--account", JULIA_EMAIL,
         "--params", json.dumps({"userId": "me", "q": "from:albertsons verification code newer_than:2m", "maxResults": 1})],
        capture_output=True, text=True, env=env
    )
    if r.returncode != 0:
        return None
    data = json.loads(r.stdout)
    messages = data.get("messages", [])
    if not messages:
        return None
    msg_id = messages[0]["id"]
    r = subprocess.run(
        [GWS, "gmail", "users", "messages", "get",
         "--account", JULIA_EMAIL,
         "--params", json.dumps({"userId": "me", "id": msg_id, "format": "full"})],
        capture_output=True, text=True, env=env
    )
    if r.returncode != 0:
        return None
    msg_data = json.loads(r.stdout)
    internal_date = int(msg_data.get("internalDate", "0")) / 1000
    if internal_date < min_time:
        return None

    def find_body(payload):
        if payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
        for part in payload.get("parts", []):
            result = find_body(part)
            if result:
                return result
        return None

    body = find_body(msg_data["payload"])
    if not body:
        return None
    stripped = re.sub(r'<[^>]+>', ' ', body)
    match = re.search(r'(?:verification|following code)[^0-9]*(\d{6})', stripped, re.IGNORECASE)
    if match:
        return match.group(1)
    codes = re.findall(r'\b(\d{6})\b', stripped)
    real_codes = [c for c in codes if c not in ['757575', '000000', '999999', '333333', '666666']]
    if real_codes:
        return real_codes[-1]
    return None

def step(msg):
    print(f"[*] {msg}")

def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)

# ============================================================
# Main flow
# ============================================================
def login():
    """Login via API and establish browser session. Returns True on success."""

    # Check if already logged in
    step("Checking existing session")
    pt_nav(f"{SM_BASE}/account/sign-in.html")
    time.sleep(8)
    page = pt_eval("document.body.innerText.slice(0, 500)")
    if "Julia" in page:
        step("Already logged in as Julia Joy")
        return True

    # Dismiss cookie overlay
    pt_eval('''(function(){
      document.cookie="OptanonAlertBoxClosed="+new Date().toISOString()+"; path=/; domain=.starmarket.com; max-age=31536000";
      var s=document.querySelector("#onetrust-consent-sdk"); if(s) s.remove();
      return "done";
    })()''')
    time.sleep(1)

    # Auth API
    step("Authenticating via API")
    auth = browser_xhr("/abs/pub/cnc/csmsservice/api/csms/authn?mode=nonotp",
                       {"userId": SM_USER, "context": {"deviceToken": DEVICE_TOKEN}}, "__auth")
    state_token = auth.get("stateToken")
    okta_id = auth.get("oktaId")
    if not state_token:
        fail(f"Auth failed: {json.dumps(auth)[:200]}")

    # Password verify
    step("Verifying password")
    pw = browser_xhr("/abs/pub/cnc/csmsservice/api/csms/authn/factors/password/verify",
                     {"stateToken": state_token, "passCode": SM_PASS, "avsi": "Y", "id": okta_id}, "__pw")

    session_token = pw.get("sessionToken")

    if pw.get("status") == "MFA_REQUIRED":
        step("MFA required -- sending email verification code")
        state_token = pw.get("stateToken", state_token)
        expires_at = pw.get("expiresAt", "")

        # Find email factor
        email_factor = None
        for f in pw.get("factors", []):
            if f.get("factorType") == "email":
                email_factor = f["id"]
                break
        if not email_factor:
            fail("No email factor in MFA response")

        # Send code
        send_time = time.time()
        send = browser_xhr(
            f"/abs/pub/cnc/csmsservice/api/csms/authn/factors/{email_factor}/send",
            {"stateToken": state_token, "oktaId": okta_id, "expiresAt": expires_at, "loginId": SM_USER},
            "__send"
        )
        if send.get("errors"):
            fail(f"Send code failed: {json.dumps(send)[:200]}")
        step(f"Verification code sent to email (status: {send.get('status', '?')})")

        # Fetch code from email
        code = None
        for attempt in range(12):
            time.sleep(5)
            code = get_verification_code(send_time)
            if code:
                step(f"Got verification code: {code}")
                break
            step(f"Waiting for email... ({attempt+1}/12)")
        if not code:
            fail("Could not get verification code from email")

        # Verify code
        step(f"Verifying code: {code}")
        verify = browser_xhr(
            f"/abs/pub/cnc/csmsservice/api/csms/authn/factors/{email_factor}/verify",
            {"stateToken": state_token, "passCode": code},
            "__verify"
        )
        if verify.get("status") != "SUCCESS":
            fail(f"Code verification failed: {json.dumps(verify)[:200]}")
        session_token = verify.get("sessionToken")

    elif pw.get("status") == "SUCCESS":
        step("Login successful (no MFA needed)")
    else:
        fail(f"Unexpected password verify status: {pw.get('status')} - {json.dumps(pw)[:200]}")

    if not session_token:
        fail("No session token obtained")

    # Establish browser session
    step("Establishing browser session")
    result = pt_eval(f'''(function(){{
      if(!window.SWY || !window.SWY.OKTA || !window.SWY.OKTA.autoSignInWithSessionToken) {{
        return "SWY.OKTA not available";
      }}
      window.SWY.OKTA.autoSignInWithSessionToken("{session_token}");
      return "ok";
    }})()''')

    if result != "ok":
        fail(f"autoSignInWithSessionToken failed: {result}")

    time.sleep(8)

    # Verify we're logged in
    page = pt_eval("document.body.innerText.slice(0, 500)")
    if "Julia" in page or "julia" in page.lower():
        step("Logged in as Julia Joy")
        return True
    else:
        step(f"Login may have failed. Page: {page[:200]}")
        return False


def get_orders():
    """Navigate to orders page and return list of order IDs."""
    step("Navigating to orders page")
    pt_nav(f"{SM_BASE}/order-account/orders")

    # Wait for order links to appear (Angular SPA needs time to render)
    orders = []
    for attempt in range(6):
        time.sleep(5)
        orders_raw = pt_eval(r'''(function(){
          var links = document.querySelectorAll('a[href*="/order-account/orders/"]');
          var orders = [];
          links.forEach(function(l){
            var match = l.href.match(/orders\/(\d+)/);
            if(match) {
              // Walk up to find parent with order summary text
              var el = l;
              for(var i = 0; i < 5; i++) { if(el.parentElement) el = el.parentElement; }
              var text = el.innerText.replace(/\s+/g, ' ').trim().slice(0, 150);
              orders.push({id: match[1], text: text});
            }
          });
          return JSON.stringify(orders);
        })()''')
        try:
            orders = json.loads(orders_raw)
        except:
            orders = []
        if orders:
            break
        url = pt_eval("window.location.href")
        step(f"Waiting for orders to load... ({attempt+1}/6) URL: {url}")

    return orders


def reorder(order_id):
    """Navigate to order detail and click Reorder button."""
    step(f"Navigating to order #{order_id}")
    pt_nav(f"{SM_BASE}/order-account/orders/{order_id}")
    time.sleep(10)

    # Get order details
    details = pt_eval("document.body.innerText.slice(0, 1500)")
    step("Order page loaded")

    # Extract item count and total from the page text
    items_match = re.search(r'(\d+)\s*items?\s*[:\xb7\u2022]\s*\$[\d.]+', details)
    if items_match:
        step(f"Order has {items_match.group(0)}")

    # Click Reorder
    step("Clicking Reorder button")
    result = js_click('''(function(){
      var btns = document.querySelectorAll("button");
      var target = null;
      btns.forEach(function(b){
        var text = b.textContent.trim().toLowerCase();
        if((text.indexOf("re-order") > -1 || text.indexOf("reorder") > -1) && b.getBoundingClientRect().height > 0)
          target = b;
      });
      return target;
    })()''')
    step(f"Reorder click result: {result}")

    time.sleep(10)

    # Verify items were added to cart
    step("Verifying cart contents")
    pt_nav(f"{SM_BASE}/erums/cart")
    time.sleep(10)

    cart_page = pt_eval("document.body.innerText.slice(0, 1000)")
    cart_match = re.search(r'Cart\s*\((\d+)\)', cart_page)
    total_match = re.search(r'total cart value is \$([\d.]+)', cart_page)

    if cart_match:
        item_count = cart_match.group(1)
        total = total_match.group(1) if total_match else "unknown"
        step(f"Cart has {item_count} items, total ${total}")
        return {"items": int(item_count), "total": total}

    if "cart" in cart_page.lower() and "$" in cart_page:
        step("Items appear to be in cart")
        return {"items": 0, "total": "unknown"}

    step(f"Cart verification unclear. Page: {cart_page[:200]}")
    return None


def main():
    parser = argparse.ArgumentParser(description="Star Market grocery reorder")
    parser.add_argument("--order-id", help="Specific order ID to reorder (default: most recent)")
    parser.add_argument("--dry-run", action="store_true", help="Login and show orders without reordering")
    args = parser.parse_args()

    if not SM_PASS:
        fail("STARMARKET_PASSWORD not set in environment")

    # Step 1: Login
    if not login():
        fail("Login failed")

    # Step 2: Get orders
    orders = get_orders()
    if not orders:
        fail("No orders found")

    step(f"Found {len(orders)} orders:")
    for o in orders[:5]:
        print(f"    #{o['id']}: {o['text']}")

    # Step 3: Reorder
    order_id = args.order_id or orders[0]["id"]

    if args.dry_run:
        step(f"DRY RUN -- would reorder #{order_id}")
        print(json.dumps({"status": "dry-run", "order_id": order_id, "orders": orders[:5]}))
        return

    step(f"Reordering #{order_id}")
    result = reorder(order_id)
    if result:
        print(f"\n[OK] Reorder #{order_id} complete. {result.get('items', '?')} items in cart, total ${result.get('total', '?')} (NOT checked out).")
        print(json.dumps({"status": "success", "order_id": order_id, "cart": result}))
    else:
        fail(f"Reorder #{order_id} may have failed")


if __name__ == "__main__":
    main()
