#!/usr/bin/env python3
"""iRobot Cloud API client — get Roomba status via cloud for newer firmware robots.

Usage:
    irobot-cloud.py login          # Login and cache credentials
    irobot-cloud.py robots         # List robots with passwords
    irobot-cloud.py status [name]  # Get robot status via AWS IoT shadow

Requires: IROBOT_EMAIL and IROBOT_PASSWORD env vars (or in .secrets-cache).
"""

import json
import hashlib
import hmac
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

CONFIG_DIR = os.path.expanduser("~/.config/irobot-cloud")
TOKEN_FILE = os.path.join(CONFIG_DIR, "session.json")
ROBOTS_FILE = os.path.join(CONFIG_DIR, "robots.json")
TOKEN_TTL = 3600  # 1 hour — AWS temp creds expire

DISCOVERY_URL = "https://disc-prod.iot.irobotapi.com/v1/discover/endpoints?country_code=US"
APP_ID = "IOS-F700B76F-80EE-4AB9-9B02-34B210F3B148"

SECRETS_FILE = os.path.expanduser("~/.openclaw/.secrets-cache")


def load_secrets():
    """Load secrets from .secrets-cache if not already in env."""
    if os.environ.get("IROBOT_EMAIL"):
        return
    if not os.path.exists(SECRETS_FILE):
        return
    with open(SECRETS_FILE) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)


def http_get(url, headers=None):
    """Simple GET request."""
    req = urllib.request.Request(url, headers=headers or {})
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read().decode())


def http_post(url, data=None, json_body=None, headers=None):
    """Simple POST request."""
    hdrs = headers or {}
    if json_body is not None:
        body = json.dumps(json_body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    elif data is not None:
        body = urllib.parse.urlencode(data).encode()
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    else:
        body = b""
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read().decode())


def discover():
    """Get iRobot API endpoints."""
    return http_get(DISCOVERY_URL)


def gigya_login(api_key, datacenter, email, password):
    """Login via Gigya (SAP Customer Data Cloud)."""
    url = f"https://accounts.{datacenter}/accounts.login"
    return http_post(url, data={
        "apiKey": api_key,
        "loginID": email,
        "password": password,
        "targetEnv": "mobile",
        "format": "json",
    })


def irobot_login(http_base, uid, uid_sig, timestamp):
    """Exchange Gigya token for iRobot credentials."""
    url = f"{http_base}/v2/login"
    return http_post(url, json_body={
        "app_id": APP_ID,
        "assume_robot_ownership": "0",
        "gigya": {
            "signature": uid_sig,
            "timestamp": timestamp,
            "uid": uid,
        },
    })


def full_login():
    """Complete login flow: discovery → Gigya → iRobot."""
    load_secrets()
    email = os.environ.get("IROBOT_EMAIL", "")
    password = os.environ.get("IROBOT_PASSWORD", "")
    if not email or not password:
        print(json.dumps({"error": "missing_credentials",
                          "message": "Set IROBOT_EMAIL and IROBOT_PASSWORD"}))
        sys.exit(1)

    # Step 1: Discovery
    disc = discover()
    # Find the deployment
    deploy_key = list(disc.get("deployments", {}).keys())[0]
    deploy = disc["deployments"][deploy_key]
    http_base = deploy["httpBase"]
    gigya_key = disc["gigya"]["api_key"]
    gigya_dc = disc["gigya"]["datacenter_domain"]

    # Step 2: Gigya login
    gigya_resp = gigya_login(gigya_key, gigya_dc, email, password)
    if gigya_resp.get("errorCode", 0) != 0:
        print(json.dumps({"error": "gigya_login_failed",
                          "message": gigya_resp.get("errorMessage", str(gigya_resp))}))
        sys.exit(1)

    uid = gigya_resp["UID"]
    uid_sig = gigya_resp["UIDSignature"]
    sig_ts = gigya_resp["signatureTimestamp"]

    # Step 3: iRobot login
    irobot_resp = irobot_login(http_base, uid, uid_sig, sig_ts)
    if "credentials" not in irobot_resp:
        print(json.dumps({"error": "irobot_login_failed",
                          "message": str(irobot_resp)[:300]}))
        sys.exit(1)

    # Cache session
    os.makedirs(CONFIG_DIR, exist_ok=True)
    session = {
        "credentials": irobot_resp["credentials"],
        "robots": irobot_resp.get("robots", {}),
        "uid": uid,
        "http_base": http_base,
        "http_base_auth": deploy.get("httpBaseAuth", ""),
        "aws_region": deploy.get("awsRegion", "us-east-1"),
        "mqtt_endpoint": deploy.get("mqtt", ""),
        "cached_at": time.time(),
    }
    with open(TOKEN_FILE, "w") as f:
        json.dump(session, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)

    # Also save robots separately for easy reference
    with open(ROBOTS_FILE, "w") as f:
        json.dump(irobot_resp.get("robots", {}), f, indent=2)
    os.chmod(ROBOTS_FILE, 0o600)

    return session


def get_session():
    """Get cached session or login."""
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE) as f:
                session = json.load(f)
            if time.time() - session.get("cached_at", 0) < TOKEN_TTL:
                return session
        except (json.JSONDecodeError, KeyError):
            pass
    return full_login()


def aws_sig_v4(method, url, headers, body, region, access_key, secret_key, session_token, service="execute-api"):
    """Compute AWS Signature V4 for a request."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = parsed.hostname
    path = parsed.path or "/"
    query = parsed.query

    now = datetime.now(timezone.utc)
    datestamp = now.strftime("%Y%m%d")
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")

    # Canonical headers
    canon_headers = f"host:{host}\nx-amz-date:{amz_date}\nx-amz-security-token:{session_token}\n"
    signed_headers = "host;x-amz-date;x-amz-security-token"

    payload_hash = hashlib.sha256((body or "").encode()).hexdigest()

    canonical_request = f"{method}\n{path}\n{query}\n{canon_headers}\n{signed_headers}\n{payload_hash}"

    scope = f"{datestamp}/{region}/{service}/aws4_request"
    string_to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}"

    def sign(key, msg):
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k_date = sign(f"AWS4{secret_key}".encode(), datestamp)
    k_region = sign(k_date, region)
    k_service = sign(k_region, service)
    k_signing = sign(k_service, "aws4_request")

    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

    auth_header = (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
                   f"SignedHeaders={signed_headers}, Signature={signature}")

    headers["Authorization"] = auth_header
    headers["x-amz-date"] = amz_date
    headers["x-amz-security-token"] = session_token
    return headers


def cmd_login():
    """Login and show result."""
    session = full_login()
    robots = session.get("robots", {})
    print(json.dumps({
        "success": True,
        "robots_count": len(robots),
        "robots": {k: {"name": v.get("name"), "sku": v.get("sku")}
                   for k, v in robots.items()},
    }, indent=2))


def cmd_robots():
    """List robots with their BLIDs and passwords."""
    session = get_session()
    robots = session.get("robots", {})
    for blid, info in robots.items():
        print(json.dumps({
            "blid": blid,
            "name": info.get("name"),
            "sku": info.get("sku"),
            "software": info.get("softwareVer"),
            "password": info.get("password"),
        }))


def cmd_status(name_filter=None):
    """Get robot status via iRobot cloud mission history.

    Note: AWS IoT shadow REST API returns 403 — the temp credentials from
    iRobot login don't have iot:GetThingShadow permission. Real-time shadow
    access would require MQTT WebSocket with SigV4 presigned URL. Mission
    history is the best we can do via REST.
    """
    session = get_session()
    robots = session.get("robots", {})
    creds = session.get("credentials", {})
    region = session.get("aws_region", "us-east-1")
    http_base = session.get("http_base_auth", "")

    if not creds or not http_base:
        print(json.dumps({"error": "no_credentials"}))
        sys.exit(1)

    for blid, info in robots.items():
        rname = info.get("name", blid)
        if name_filter and name_filter.lower() not in rname.lower():
            continue

        history_url = f"{http_base}/v1/{blid}/missionhistory"
        headers = {}
        try:
            headers = aws_sig_v4(
                "GET", history_url, headers, "",
                region, creds["AccessKeyId"],
                creds["SecretKey"], creds["SessionToken"],
            )
            req = urllib.request.Request(history_url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=15)
            missions = json.loads(resp.read().decode())

            if not missions:
                print(json.dumps({"name": rname, "blid": blid, "error": "no_missions"}))
                continue

            m = missions[0]
            result = {
                "name": rname,
                "blid": blid,
                "lastMission": m.get("done", "unknown"),
                "durationMin": m.get("durationM"),
                "sqft": m.get("sqft"),
                "initiator": m.get("initiator"),
                "startTime": m.get("startTime"),
                "missions": m.get("nMssn"),
            }
            print(json.dumps(result))

        except urllib.error.HTTPError as e:
            print(json.dumps({
                "name": rname,
                "blid": blid,
                "error": f"HTTP {e.code}",
                "message": e.read().decode()[:200],
            }))
        except Exception as e:
            print(json.dumps({
                "name": rname,
                "blid": blid,
                "error": str(e),
            }))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "login":
        cmd_login()
    elif cmd == "robots":
        cmd_robots()
    elif cmd == "status":
        name = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_status(name)
    else:
        print(f"Usage: irobot-cloud.py [login|robots|status [name]]", file=sys.stderr)
        sys.exit(1)
