"""
mitmproxy addon: selectively intercept iRobot API traffic.

- Intercepts (decrypts) content-prod, ecomm, unauth3, auth3 traffic
- Passes through everything else (Apple, Firebase, etc.) so the app works
- Logs all intercepted requests to captured_requests.jsonl

Usage: mitmweb -p 8080 --script selective_intercept.py
"""

import json
import time
from pathlib import Path
from mitmproxy import tls, http, ctx

LOG_FILE = Path(__file__).parent / "captured_requests.jsonl"

# Domains we want to intercept (decrypt)
INTERCEPT_DOMAINS = {
    "content-prod.iot.irobotapi.com",
    "ecomm.prod.user-services.irobotapi.com",
    "unauth3.prod.iot.irobotapi.com",
    "auth3.prod.iot.irobotapi.com",
    "unauth1.prod.iot.irobotapi.com",
    "auth1.prod.iot.irobotapi.com",
    "disc-prod.iot.irobotapi.com",
    "accounts.us1.gigya.com",
    "certificatefactory.prod.security.irobotapi.com",
}


class SelectiveInterceptor:
    def tls_clienthello(self, data: tls.ClientHelloData):
        sni = data.context.server.address
        if sni:
            host, port = sni
            if host not in INTERCEPT_DOMAINS:
                # Pass through non-iRobot traffic untouched
                data.ignore_connection = True
                ctx.log.info(f"[PASS] {host}:{port}")
            else:
                ctx.log.info(f"[INTERCEPT] {host}:{port}")

    def response(self, flow: http.HTTPFlow):
        """Log all intercepted request/response pairs."""
        req = flow.request
        resp = flow.response

        # Try to parse response body as JSON
        resp_body = None
        if resp and resp.content:
            try:
                resp_body = json.loads(resp.content)
            except (json.JSONDecodeError, UnicodeDecodeError):
                resp_body = f"<binary {len(resp.content)} bytes>"

        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "method": req.method,
            "url": req.pretty_url,
            "host": req.host,
            "path": req.path,
            "req_headers": dict(req.headers),
            "req_body": req.get_text()[:2000] if req.content else None,
            "status": resp.status_code if resp else None,
            "resp_headers": dict(resp.headers) if resp else None,
            "resp_body": resp_body if not isinstance(resp_body, str) else resp_body[:2000],
        }

        ctx.log.info(f"[LOG] {req.method} {req.pretty_url} → {resp.status_code if resp else '?'}")

        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")


addons = [SelectiveInterceptor()]
