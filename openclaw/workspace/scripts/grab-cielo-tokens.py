#!/usr/bin/env python3
"""Capture Cielo tokens from an authenticated browser session via CDP."""
import json, asyncio, websockets, subprocess, time, os, sys

CONFIG_FILE = os.path.expanduser("~/.config/cielo/config.json")

async def grab(cdp_port):
    tabs_raw = subprocess.check_output(["curl", "-s", f"http://localhost:{cdp_port}/json"], text=True)
    tabs = json.loads(tabs_raw)
    cielo_tab = next((t for t in tabs if "cielowigle" in t.get("url", "")), None)
    if not cielo_tab:
        print("No Cielo tab found")
        sys.exit(1)

    tab_url = cielo_tab["url"]
    ws_url = cielo_tab["webSocketDebuggerUrl"]
    print(f"Tab: {tab_url}")

    async with websockets.connect(ws_url, max_size=10*1024*1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
        await ws.recv()

        # Reload to force fresh API calls
        await ws.send(json.dumps({"id": 2, "method": "Page.reload", "params": {"ignoreCache": True}}))

        print("Waiting for smartcielo.com requests...")

        deadline = time.time() + 25
        token = None
        session_id = None
        user_id = None

        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=3)
            except asyncio.TimeoutError:
                continue

            msg = json.loads(raw)
            method = msg.get("method", "")

            if method == "Network.requestWillBeSent":
                req = msg["params"]["request"]
                req_url = req["url"]
                headers = req.get("headers", {})

                if "smartcielo.com" in req_url:
                    print(f"  REQ: {req_url[:100]}")
                    auth = headers.get("authorization", "") or headers.get("Authorization", "")
                    if auth and len(auth) > 20:
                        token = auth
                        print(f"  -> Got accessToken ({len(auth)} chars)")

                    if "sessionId=" in req_url:
                        import urllib.parse as up
                        qs = up.parse_qs(up.urlparse(req_url).query)
                        if "sessionId" in qs:
                            session_id = qs["sessionId"][0]
                            print(f"  -> Got sessionId: {session_id[:40]}...")

            if method == "Network.responseReceived":
                resp_url = msg["params"]["response"]["url"]
                rid = msg["params"]["requestId"]

                if "smartcielo.com" in resp_url and "device" in resp_url.lower():
                    try:
                        await ws.send(json.dumps({"id": 50, "method": "Network.getResponseBody", "params": {"requestId": rid}}))
                        while True:
                            r = json.loads(await ws.recv())
                            if r.get("id") == 50:
                                break
                        body = r.get("result", {}).get("body", "")
                        data = json.loads(body)
                        devs = data.get("data", {}).get("listDevices", [])
                        if devs:
                            user_id = devs[0].get("userId", "")
                            print(f"  -> Got userId: {user_id}")
                    except Exception as e:
                        print(f"  -> Error getting response body: {e}")

            if token and session_id and user_id:
                break

        if not token:
            print("\nNo token captured. The session may have expired.")
            sys.exit(1)

        # Save
        config = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE) as f:
                config = json.load(f)

        config["accessToken"] = token
        if session_id:
            config["sessionId"] = session_id
        if user_id:
            config["userId"] = user_id
        config["apiKey"] = "3iCWYuBqpY2g7yRq3yyTk1XCS4CMjt1n9ECCjdpd"
        config["lastRefresh"] = int(time.time() * 1000)

        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        os.chmod(CONFIG_FILE, 0o600)

        print(f"\nSAVED to {CONFIG_FILE}")
        print(f"accessToken: {token[:40]}...")
        print(f"sessionId: {session_id or 'n/a'}")
        print(f"userId: {user_id or 'n/a'}")
        print(f"refreshToken: {config.get('refreshToken', 'none')}")

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 61293
    asyncio.run(grab(port))
