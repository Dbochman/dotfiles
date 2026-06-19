---
name: coinbase-cdp-ed25519-auth
description: |
  Diagnose and implement Coinbase CDP JWT authentication for downloaded API key JSON files.
  Use when a key has `id` plus a base64 `privateKey` instead of `name` plus a PEM key,
  an existing ES256 signer raises `KeyError: name` or cannot deserialize the key, or direct
  Coinbase App requests need Ed25519/EdDSA support without breaking legacy ECDSA keys.
---

# Coinbase CDP Ed25519 Authentication

## Identify the key format

Inspect field names and lengths without printing credential values:

- Treat `name` plus a PEM `privateKey` as a legacy ECDSA key and sign with ES256.
- Treat `id` plus a base64 `privateKey` that decodes to 64 bytes as Ed25519. Coinbase encodes a 32-byte seed followed by a 32-byte public key.
- Reject ambiguous, malformed, or incorrectly sized keys instead of guessing.

Keep key files outside source control and mode `0600`. Do not log the key ID, private key, JWT, account balances, or transaction data during validation.

## Add Ed25519 without regressing ECDSA

Preserve the existing ECDSA branch. For Ed25519:

1. Strictly base64-decode `privateKey` and require 64 bytes.
2. Construct the signing key from the first 32 bytes with `Ed25519PrivateKey.from_private_bytes`.
3. Set JWT header fields `alg: EdDSA`, `kid: <id>`, `typ: JWT`, and a cryptographically random `nonce`.
4. Set claims `sub: <id>`, `iss: cdp`, `aud: [cdp_service]`, `nbf: now`, `exp: now + 120`, and `uri: "METHOD api.coinbase.com/path"`.
5. Exclude the query string from the `uri` claim even when the HTTP request includes one.
6. Sign the ASCII `base64url(header).base64url(payload)` input. Ed25519 returns the raw 64-byte JWT signature directly.

Minimal signing core:

```python
raw_key = base64.b64decode(key_secret, validate=True)
if len(raw_key) != 64:
    raise ValueError("Ed25519 privateKey must decode to 64 bytes")
private_key = ed25519.Ed25519PrivateKey.from_private_bytes(raw_key[:32])

header = {"alg": "EdDSA", "kid": key_id, "typ": "JWT", "nonce": secrets.token_hex(16)}
payload = {
    "sub": key_id,
    "iss": "cdp",
    "aud": ["cdp_service"],
    "nbf": now,
    "exp": now + 120,
    "uri": f"{method} api.coinbase.com{path}",
}
signing_input = f"{b64url_json(header)}.{b64url_json(payload)}"
token = f"{signing_input}.{b64url(private_key.sign(signing_input.encode('ascii')))}"
```

Do not reuse the ECDSA DER-to-raw conversion for Ed25519. Keep that conversion only in the ES256 branch.

## Validate safely

1. Unit-test both formats with generated keys and verify each JWT signature with its public key.
2. Test the actual credential only against a read-only endpoint such as `GET /v2/accounts?limit=1`.
3. Report only the HTTP status, returned item count, and pagination presence.
4. Back up the deployed key and state before cutover; install the new key atomically with mode `0600`.
5. Run the exact scheduled interpreter/service path after deployment, not only a local development environment.

A 401 after correct key parsing commonly indicates a missing `aud` claim, an incorrect `kid`, an expired token, or a `uri` claim that includes the query string. Coinbase App SDK guidance may still require ECDSA; distinguish SDK compatibility from a direct REST request and prove the intended endpoint with a read-only test.

## References

- [Coinbase CDP API authentication](https://docs.cdp.coinbase.com/api-reference/v2/authentication)
- [Coinbase App API key authentication](https://docs.cdp.coinbase.com/coinbase-app/authentication-authorization/api-key-authentication)
- [Coinbase authentication overview and algorithm compatibility](https://docs.cdp.coinbase.com/get-started/authentication/overview)
