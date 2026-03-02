#!/usr/bin/env python3
"""Capture Cielo tokens from an authenticated browser session via CDP.

Usage:
  grab-cielo-tokens.py [CDP_PORT]              # Reload dashboard and capture tokens
  grab-cielo-tokens.py [CDP_PORT] --passive    # Watch live traffic without reloading
                                                # (use during login to capture refreshToken)

Uses CDP Fetch domain to intercept response bodies before the page consumes them.
This is necessary because Network.getResponseBody returns empty for fetch() API responses.
Cielo's /auth/login nests tokens inside data.user (not directly under data).
"""
import json, asyncio, websockets, subprocess, time, os, sys

CONFIG_FILE = os.path.expanduser("~/.config/cielo/config.json")

async def grab(cdp_port, passive=False):
    tabs_raw = subprocess.check_output(["curl", "-s", f"http://localhost:{cdp_port}/json"], text=True)
    tabs = json.loads(tabs_raw)
    cielo_tab = next((t for t in tabs if "cielowigle" in t.get("url", "")), None)
    if not cielo_tab:
        print("No Cielo tab found")
        sys.exit(1)

    tab_url = cielo_tab["url"]
    ws_url = cielo_tab["webSocketDebuggerUrl"]
    print(f"Tab: {tab_url}")
    print(f"Mode: {'passive (watching live traffic)' if passive else 'active (reload)'}")

    async with websockets.connect(ws_url, max_size=10*1024*1024) as ws:
        # Enable Network for request headers (accessToken from Authorization header)
        await ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
        await ws.recv()

        # Enable Fetch domain to intercept response bodies BEFORE the page consumes them
        await ws.send(json.dumps({
            "id": 2,
            "method": "Fetch.enable",
            "params": {
                "patterns": [
                    {"urlPattern": "*smartcielo.com*", "requestStage": "Response"}
                ]
            }
        }))
        await ws.recv()

        if not passive:
            await ws.send(json.dumps({"id": 3, "method": "Page.reload", "params": {"ignoreCache": True}}))

        print("Waiting for smartcielo.com requests...")

        deadline = time.time() + (45 if passive else 25)
        token = None
        session_id = None
        user_id = None
        refresh_token = None
        msg_id_counter = 10

        # Track pending Fetch.getResponseBody calls
        pending_fetch_body = {}  # CDP msg id -> {"request_id": str, "url": str}

        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=3)
            except asyncio.TimeoutError:
                continue

            msg = json.loads(raw)

            # Handle Fetch.getResponseBody replies
            if "id" in msg and msg["id"] in pending_fetch_body:
                info = pending_fetch_body.pop(msg["id"])
                result = msg.get("result", {})
                body = result.get("body", "")

                # Decode base64 if needed
                if result.get("base64Encoded") and body:
                    import base64
                    body = base64.b64decode(body).decode("utf-8", errors="replace")

                if body:
                    try:
                        data = json.loads(body)

                        # Build list of places to look for tokens
                        # Cielo /auth/login nests tokens inside data.user
                        sources = []
                        inner = data.get("data", {})
                        if isinstance(inner, dict):
                            if isinstance(inner.get("user"), dict):
                                sources.append(("data.user", inner["user"]))
                            sources.append(("data", inner))
                        sources.append(("top", data))

                        for source_name, source in sources:
                            if not isinstance(source, dict):
                                continue
                            if source.get("refreshToken") and not refresh_token:
                                refresh_token = source["refreshToken"]
                                print(f"  -> Got refreshToken from {source_name} ({len(refresh_token)} chars)")
                            if source.get("accessToken") and not token:
                                token = source["accessToken"]
                                print(f"  -> Got accessToken from {source_name} ({len(token)} chars)")
                            if source.get("sessionId") and not session_id:
                                session_id = source["sessionId"]
                                print(f"  -> Got sessionId from {source_name}")
                            if source.get("userId") and not user_id:
                                user_id = source["userId"]
                                print(f"  -> Got userId from {source_name}: {user_id}")

                        # Device list fallback for userId
                        if isinstance(inner, dict):
                            devs = inner.get("listDevices", [])
                            if devs and not user_id:
                                user_id = devs[0].get("userId", "")
                                if user_id:
                                    print(f"  -> Got userId from device list: {user_id}")
                    except json.JSONDecodeError:
                        pass

                # Continue the paused request so the page gets its response
                msg_id_counter += 1
                await ws.send(json.dumps({
                    "id": msg_id_counter,
                    "method": "Fetch.continueRequest",
                    "params": {"requestId": info["request_id"]}
                }))
                continue

            method = msg.get("method", "")

            # Fetch.requestPaused — response is ready but paused before delivery to page
            if method == "Fetch.requestPaused":
                params = msg["params"]
                req_url = params.get("request", {}).get("url", "")
                fetch_rid = params["requestId"]
                status = params.get("responseStatusCode", 0)

                if "smartcielo.com" in req_url and status >= 200:
                    print(f"  RESP: {req_url[:100]} (status={status})")

                    # Get the response body while it's still paused
                    msg_id_counter += 1
                    pending_fetch_body[msg_id_counter] = {
                        "request_id": fetch_rid,
                        "url": req_url
                    }
                    await ws.send(json.dumps({
                        "id": msg_id_counter,
                        "method": "Fetch.getResponseBody",
                        "params": {"requestId": fetch_rid}
                    }))
                else:
                    # Not interesting — continue immediately
                    msg_id_counter += 1
                    await ws.send(json.dumps({
                        "id": msg_id_counter,
                        "method": "Fetch.continueRequest",
                        "params": {"requestId": fetch_rid}
                    }))

            # Network.requestWillBeSent — capture token from Authorization header
            elif method == "Network.requestWillBeSent":
                req = msg["params"]["request"]
                req_url = req["url"]
                headers = req.get("headers", {})

                if "smartcielo.com" in req_url:
                    print(f"  REQ: {req_url[:100]}")
                    auth = headers.get("authorization", "") or headers.get("Authorization", "")
                    if auth and len(auth) > 20:
                        token = auth
                        print(f"  -> Got accessToken from header ({len(auth)} chars)")

                    if "sessionId=" in req_url:
                        import urllib.parse as up
                        qs = up.parse_qs(up.urlparse(req_url).query)
                        if "sessionId" in qs:
                            session_id = qs["sessionId"][0]
                            print(f"  -> Got sessionId: {session_id[:40]}...")

            # Break conditions
            if passive:
                if refresh_token and token:
                    break
            else:
                if token and session_id and user_id:
                    break

        # Disable Fetch interception so browser behaves normally
        await ws.send(json.dumps({"id": 999, "method": "Fetch.disable"}))

        if not token:
            print("\nNo token captured.")
            if passive:
                print("(passive mode: login may not have fired yet)")
            else:
                print("The session may have expired.")
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
        if refresh_token:
            config["refreshToken"] = refresh_token
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
        print(f"refreshToken: {refresh_token[:40] + '...' if refresh_token else 'not captured'}")

if __name__ == "__main__":
    args = sys.argv[1:]
    passive = "--passive" in args
    args = [a for a in args if a != "--passive"]
    port = int(args[0]) if args else 61293
    asyncio.run(grab(port, passive=passive))
