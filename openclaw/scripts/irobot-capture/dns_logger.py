"""
mitmproxy addon that logs all TLS Client Hello SNI hostnames
without intercepting the connection (passthrough mode).

Usage: mitmdump --mode transparent --ssl-insecure --script dns_logger.py
   OR: mitmdump -p 8080 --script dns_logger.py --set ignore_hosts='.*'

This lets the app work normally while we record every host it contacts.
"""

import json
import time
from pathlib import Path
from mitmproxy import tls, connection, ctx

LOG_FILE = Path(__file__).parent / "captured_hosts.jsonl"
SEEN = set()


class SNILogger:
    def tls_clienthello(self, data: tls.ClientHelloData):
        sni = data.context.server.address
        if sni:
            host, port = sni
            key = f"{host}:{port}"
            if key not in SEEN:
                SEEN.add(key)
                entry = {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "host": host,
                    "port": port,
                }
                ctx.log.info(f"[SNI] {host}:{port}")
                with open(LOG_FILE, "a") as f:
                    f.write(json.dumps(entry) + "\n")

        # Ignore (passthrough) all connections so the app works normally
        data.ignore_connection = True


addons = [SNILogger()]
