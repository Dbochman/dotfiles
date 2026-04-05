#!/usr/bin/env python3
"""Connect to iRobot AWS IoT MQTT via WebSocket with SigV4 presigned URL.

Subscribes to the robot's shadow topics to get real-time state.

Usage:
    mqtt_shadow.py [robot_name]    # Subscribe to shadow updates
    mqtt_shadow.py --list          # List robots
"""

import hashlib
import hmac
import json
import os
import ssl
import struct
import sys
import time
import urllib.parse
from datetime import datetime, timezone

CONFIG_DIR = os.path.expanduser("~/.config/irobot-cloud")
TOKEN_FILE = os.path.join(CONFIG_DIR, "session.json")

# MQTT constants
MQTT_CONNECT = 1
MQTT_CONNACK = 2
MQTT_PUBLISH = 3
MQTT_PUBACK = 4
MQTT_SUBSCRIBE = 8
MQTT_SUBACK = 9
MQTT_PINGREQ = 12
MQTT_PINGRESP = 13


def load_session():
    with open(TOKEN_FILE) as f:
        return json.load(f)


def sign(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def get_signature_key(secret_key, datestamp, region, service):
    k_date = sign(("AWS4" + secret_key).encode("utf-8"), datestamp)
    k_region = sign(k_date, region)
    k_service = sign(k_region, service)
    k_signing = sign(k_service, "aws4_request")
    return k_signing


def presign_mqtt_url(endpoint, region, access_key, secret_key, session_token):
    """Build a SigV4 presigned WebSocket URL for AWS IoT MQTT."""
    service = "iotdevicegateway"
    now = datetime.now(timezone.utc)
    datestamp = now.strftime("%Y%m%d")
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")

    scope = f"{datestamp}/{region}/{service}/aws4_request"

    # Canonical query string
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": "86400",
        "X-Amz-Security-Token": session_token,
        "X-Amz-SignedHeaders": "host",
    }
    # Must be sorted by key
    canonical_qs = "&".join(f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
                           for k, v in sorted(params.items()))

    canonical_headers = f"host:{endpoint}\n"
    signed_headers = "host"
    payload_hash = hashlib.sha256(b"").hexdigest()

    canonical_request = f"GET\n/mqtt\n{canonical_qs}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"

    string_to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}"

    signing_key = get_signature_key(secret_key, datestamp, region, service)
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()

    url = f"wss://{endpoint}/mqtt?{canonical_qs}&X-Amz-Signature={signature}"
    return url


def encode_utf8_string(s):
    """MQTT UTF-8 encoded string: 2-byte length prefix + bytes."""
    encoded = s.encode("utf-8")
    return struct.pack("!H", len(encoded)) + encoded


def build_connect_packet(client_id):
    """Build MQTT CONNECT packet."""
    # Variable header
    protocol_name = encode_utf8_string("MQTT")
    protocol_level = bytes([4])  # MQTT 3.1.1
    connect_flags = bytes([2])  # Clean session
    keep_alive = struct.pack("!H", 300)  # 5 min

    variable_header = protocol_name + protocol_level + connect_flags + keep_alive

    # Payload
    payload = encode_utf8_string(client_id)

    remaining = variable_header + payload
    # Fixed header
    fixed_header = bytes([MQTT_CONNECT << 4])
    fixed_header += encode_remaining_length(len(remaining))

    return fixed_header + remaining


def build_subscribe_packet(packet_id, topic, qos=0):
    """Build MQTT SUBSCRIBE packet."""
    variable_header = struct.pack("!H", packet_id)
    payload = encode_utf8_string(topic) + bytes([qos])
    remaining = variable_header + payload
    fixed_header = bytes([(MQTT_SUBSCRIBE << 4) | 2])
    fixed_header += encode_remaining_length(len(remaining))
    return fixed_header + remaining


def build_pingreq():
    """Build MQTT PINGREQ packet."""
    return bytes([MQTT_PINGREQ << 4, 0])


def encode_remaining_length(length):
    """Encode MQTT remaining length field."""
    encoded = bytearray()
    while True:
        byte = length % 128
        length = length // 128
        if length > 0:
            byte |= 0x80
        encoded.append(byte)
        if length == 0:
            break
    return bytes(encoded)


def decode_remaining_length(data, offset):
    """Decode MQTT remaining length, return (length, bytes_consumed)."""
    multiplier = 1
    value = 0
    idx = offset
    while True:
        byte = data[idx]
        value += (byte & 0x7F) * multiplier
        multiplier *= 128
        idx += 1
        if (byte & 0x80) == 0:
            break
    return value, idx - offset


def parse_packet(data):
    """Parse an MQTT packet, return (type, payload, total_length)."""
    if len(data) < 2:
        return None, None, 0

    pkt_type = (data[0] >> 4) & 0x0F
    remaining_len, len_bytes = decode_remaining_length(data, 1)
    total = 1 + len_bytes + remaining_len

    if len(data) < total:
        return None, None, 0

    payload = data[1 + len_bytes:total]
    return pkt_type, payload, total


def extract_publish_topic_and_message(payload, flags):
    """Extract topic and message from PUBLISH packet payload."""
    topic_len = struct.unpack("!H", payload[0:2])[0]
    topic = payload[2:2 + topic_len].decode("utf-8")
    offset = 2 + topic_len

    # QoS > 0 has packet ID
    qos = (flags >> 1) & 3
    if qos > 0:
        offset += 2

    message = payload[offset:]
    return topic, message


def main():
    import websocket

    session = load_session()
    creds = session["credentials"]
    robots = session.get("robots", {})
    region = session.get("aws_region", "us-east-1")
    mqtt_endpoint = session.get("mqtt_endpoint", "")

    if not mqtt_endpoint:
        print("No MQTT endpoint in session", file=sys.stderr)
        sys.exit(1)

    if "--list" in sys.argv:
        for blid, info in robots.items():
            print(f"  {info.get('name', '?'):15s}  BLID={blid}  SKU={info.get('sku')}")
        return

    # Pick robot
    name_filter = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else None
    target_blids = []
    for blid, info in robots.items():
        rname = info.get("name", blid)
        if name_filter is None or name_filter.lower() in rname.lower():
            target_blids.append((blid, rname))

    if not target_blids:
        print(f"No robot matching '{name_filter}'", file=sys.stderr)
        sys.exit(1)

    print(f"Connecting to MQTT: {mqtt_endpoint}")
    print(f"Subscribing to shadows for: {', '.join(n for _, n in target_blids)}")

    # Build presigned URL
    url = presign_mqtt_url(
        mqtt_endpoint, region,
        creds["AccessKeyId"], creds["SecretKey"], creds["SessionToken"],
    )

    print(f"Connecting via WebSocket...")

    ws = websocket.create_connection(
        url,
        subprotocols=["mqtt"],
        sslopt={"cert_reqs": ssl.CERT_REQUIRED},
        timeout=10,
    )

    # Send CONNECT
    client_id = f"irobot-capture-{int(time.time())}"
    ws.send(build_connect_packet(client_id), opcode=websocket.ABNF.OPCODE_BINARY)

    # Read CONNACK
    data = ws.recv()
    if isinstance(data, str):
        data = data.encode()
    pkt_type, payload, _ = parse_packet(data)
    if pkt_type == MQTT_CONNACK:
        rc = payload[1] if len(payload) > 1 else -1
        if rc == 0:
            print("✅ MQTT connected!")
        else:
            print(f"❌ CONNACK return code: {rc}")
            ws.close()
            return
    else:
        print(f"❌ Expected CONNACK, got packet type {pkt_type}")
        ws.close()
        return

    # Subscribe to shadow topics for each robot
    pkt_id = 1
    for blid, rname in target_blids:
        # Standard AWS IoT shadow topics
        topics = [
            f"$aws/things/{blid}/shadow/get/accepted",
            f"$aws/things/{blid}/shadow/get/rejected",
            f"$aws/things/{blid}/shadow/update/accepted",
            f"$aws/things/{blid}/shadow/update/rejected",
            f"$aws/things/{blid}/shadow/update/delta",
            f"$aws/things/{blid}/shadow/update/documents",
        ]

        # Also try iRobot custom topics from discovery (irbtTopics: v011-irbthbu)
        svc = robots[blid].get("svcDeplId", "v011")
        topics.append(f"{svc}-irbthbu/{blid}/status")
        topics.append(f"{svc}-irbthbu/{blid}/#")

        for topic in topics:
            print(f"  SUB: {topic}")
            ws.send(build_subscribe_packet(pkt_id, topic), opcode=websocket.ABNF.OPCODE_BINARY)
            pkt_id += 1

    # Also request the current shadow
    for blid, rname in target_blids:
        get_topic = f"$aws/things/{blid}/shadow/get"
        print(f"  PUB: {get_topic} (requesting shadow)")
        # Build a simple PUBLISH packet
        topic_bytes = encode_utf8_string(get_topic)
        msg = b""  # Empty payload triggers shadow get
        remaining = topic_bytes + msg
        fixed = bytes([MQTT_PUBLISH << 4])
        fixed += encode_remaining_length(len(remaining))
        ws.send(fixed + remaining, opcode=websocket.ABNF.OPCODE_BINARY)

    print(f"\n--- Listening for messages (Ctrl+C to stop) ---\n")

    # Read loop
    buffer = bytearray()
    last_ping = time.time()
    ws.settimeout(5)

    try:
        while True:
            try:
                data = ws.recv()
                if isinstance(data, str):
                    data = data.encode()
                buffer.extend(data)
            except websocket.WebSocketTimeoutException:
                pass

            # Parse all complete packets in buffer
            while len(buffer) >= 2:
                pkt_type_byte = buffer[0]
                pkt_type = (pkt_type_byte >> 4) & 0x0F
                flags = pkt_type_byte & 0x0F

                pkt_type_val, payload, total = parse_packet(bytes(buffer))
                if pkt_type_val is None:
                    break  # Incomplete packet

                buffer = buffer[total:]

                if pkt_type_val == MQTT_PUBLISH:
                    topic, message = extract_publish_topic_and_message(payload, flags)
                    try:
                        msg_json = json.loads(message)
                        print(f"\n📨 {topic}")
                        print(json.dumps(msg_json, indent=2)[:5000])
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        print(f"\n📨 {topic}")
                        print(f"  <binary {len(message)} bytes>")

                elif pkt_type_val == MQTT_SUBACK:
                    granted_qos = list(payload[2:]) if len(payload) > 2 else []
                    status = "✅" if all(q < 0x80 for q in granted_qos) else "❌ (rejected)"
                    print(f"  SUBACK: {status} qos={granted_qos}")

                elif pkt_type_val == MQTT_PINGRESP:
                    pass  # Expected

            # Ping to keep alive
            if time.time() - last_ping > 240:
                ws.send(build_pingreq(), opcode=websocket.ABNF.OPCODE_BINARY)
                last_ping = time.time()

    except KeyboardInterrupt:
        print("\nDisconnecting...")
    finally:
        ws.close()


if __name__ == "__main__":
    main()
