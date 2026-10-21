#!/usr/bin/env python3
"""Credential-free eMudhra TLS canary; no HTTP requests or trust-store writes.

Run with the application's actual Python interpreter. --certifi checks a
Requests-style bundle separately. A timeout/DNS failure is NOT a passing test.
Exit 0: all expected results; 1: trusted compromised chain; 2: inconclusive.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import socket
import ssl
import sys


CONTROLS = ("github.com", "pypi.org")
CANARIES = {
    "{}-{}-{}.we-have-a-problem.com".format(kind, variant, chain):
        "EM {} TLS CA - {}".format(kind.upper(), variant.upper())
    for chain in ("emudhra", "aaa-emudhra")
    for kind in ("dv", "ov", "ev")
    for variant in ("g3b", "g3c")
}


def probe(host, expected_issuer, context):
    try:
        with socket.create_connection((host, 443), timeout=8) as connection:
            with context.wrap_socket(connection, server_hostname=host) as tls:
                certificate = tls.getpeercert()
        issuer = dict(item for rdn in certificate.get("issuer", ()) for item in rdn).get("commonName")
        if expected_issuer is None:
            return {"host": host, "result": "control_passed"}
        return {"host": host, "result": "exposed" if issuer == expected_issuer else "canary_changed"}
    except ssl.SSLCertVerificationError as error:
        # Expiry/hostname/signature failures do not prove the compromised CA
        # was distrusted. Accept only issuer/trust/revocation failures.
        trust_failure = getattr(error, "verify_code", None) in (2, 19, 20, 21, 23, 27, 28)
        result = "rejected" if expected_issuer and trust_failure else "inconclusive"
        return {"host": host, "result": result, "verify_code": getattr(error, "verify_code", None)}
    except (OSError, ssl.SSLError) as error:
        return {"host": host, "result": "inconclusive", "error": type(error).__name__}


def exit_code(results):
    if not results:
        return 2
    if any(result["result"] == "exposed" for result in results):
        return 1
    if any(result["result"] not in ("rejected", "control_passed") for result in results):
        return 2
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ca-file")
    group.add_argument("--certifi", action="store_true")
    args = parser.parse_args()
    ca_file = args.ca_file
    if args.certifi:
        import certifi
        ca_file = certifi.where()
    context = ssl.create_default_context(cafile=ca_file)
    targets = [(host, None) for host in CONTROLS] + list(CANARIES.items())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda target: probe(*target, context), targets))
    code = exit_code(results)
    print(json.dumps({
        "ok": code == 0, "python": sys.version.split()[0],
        "openssl": ssl.OPENSSL_VERSION, "trust_source": ca_file or "default",
        "results": results,
    }, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
