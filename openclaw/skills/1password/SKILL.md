---
name: 1password
description: Read secrets, credentials, and payment information from the OpenClaw 1Password vault. Use when asked about passwords, API keys, credit card details, SSH keys, or any stored credentials. Also use when making purchases that require card information.
allowed-tools: Bash(1password:*)
metadata: {"openclaw":{"emoji":"🔐","requires":{"bins":["op"]}}}
---

# 1Password Vault Access

Read secrets and credentials from the OpenClaw vault via the `op` CLI. The service account has **read-only** access.

## Important: op read may hang under launchd

The `op read` command can hang indefinitely when run under launchd (the gateway process). Use the timeout wrapper pattern:

```bash
timeout 5 op read "op://OpenClaw/Item Name/field" 2>/dev/null || echo "TIMEOUT"
```

Always use `timeout` to prevent hanging. If `op read` times out, **fall back to cached values** (see Cached Secrets section below).

## Reading Secrets

### Read a specific field
```bash
timeout 5 op read "op://OpenClaw/Item Name/field" 2>/dev/null
```

### Common items in the vault

| Item | Category | Read Command |
|------|----------|-------------|
| OpenAI API Key | Password | `timeout 5 op read "op://OpenClaw/OpenAI API Key/password"` |
| ElevenLabs API Key | Password | `timeout 5 op read "op://OpenClaw/ElevenLabs API Key/password"` |
| Gateway Token | Password | `timeout 5 op read "op://OpenClaw/OpenClaw Gateway Token/password"` |
| Digital Ocean API | API Credential | `timeout 5 op read "op://OpenClaw/Digital Ocean API Credential/credential"` |
| Andre Flask Secret | Password | `timeout 5 op read "op://OpenClaw/Andre Flask Secret Key/password"` |
| Google Nest | Login | `timeout 5 op read "op://OpenClaw/Google Nest/username"` |

### Credit Card (Visa)
```bash
# Card number
timeout 5 op read "op://OpenClaw/Visa/number" 2>/dev/null

# Expiration date
timeout 5 op read "op://OpenClaw/Visa/expiry date" 2>/dev/null

# CVV
timeout 5 op read "op://OpenClaw/Visa/verification number" 2>/dev/null

# Cardholder name
timeout 5 op read "op://OpenClaw/Visa/cardholder name" 2>/dev/null
```

### List all items
```bash
timeout 5 op item list --vault OpenClaw --format json 2>/dev/null
```

### Get full item details
```bash
timeout 5 op item get "Item Name" --vault OpenClaw --format json 2>/dev/null
```

## Using Credit Card for Purchases

When making online purchases with the credit card:

1. **Read card details** — prefer cached files (faster, always available); fall back to `op read`
2. **Read billing address** from `~/.cache/openclaw-gateway/visa_billing_address`
3. **Use browser automation** to navigate to the checkout page
4. **Fill in payment fields** with the card data
5. **Always confirm with Dylan** before completing any purchase
6. **Never log or display full card numbers** — show only last 4 digits

### Reading Cached Card Details (Preferred)
```bash
# Card number
cat ~/.cache/openclaw-gateway/visa_number

# CVV
cat ~/.cache/openclaw-gateway/visa_cvv

# Cardholder name
cat ~/.cache/openclaw-gateway/visa_cardholder

# Expiry date
cat ~/.cache/openclaw-gateway/visa_expiry

# Card type
cat ~/.cache/openclaw-gateway/visa_type

# Billing address (multi-line: name, street, city/state/zip, country)
cat ~/.cache/openclaw-gateway/visa_billing_address

# Credit limit (spending cap — NEVER exceed this)
cat ~/.cache/openclaw-gateway/visa_credit_limit
```

### Purchase Safety Rules

- ALWAYS ask Dylan for confirmation before submitting any payment
- NEVER share card details in messages, logs, or chat
- When displaying card info to Dylan, mask it: `****-****-****-1234`
- If a purchase requires 2FA or SMS verification, notify Dylan
- **Check credit limit** before purchasing: `cat ~/.cache/openclaw-gateway/visa_credit_limit`
- NEVER exceed the credit limit — this is a hard spending cap set by Dylan
- Flag anything over $100 for explicit approval even if under the limit

## Cached Secrets

Secrets are cached locally to work around the launchd hang issue. The gateway wrapper refreshes them on restart when `op read` works.

### API Keys & Tokens
- `~/.cache/openclaw-gateway/gateway_token`
- `~/.cache/openclaw-gateway/openai_api_key`
- `~/.cache/openclaw-gateway/elevenlabs_api_key`

### Credit Card (Visa ending 0298)
- `~/.cache/openclaw-gateway/visa_number` — full card number
- `~/.cache/openclaw-gateway/visa_cvv` — verification number
- `~/.cache/openclaw-gateway/visa_cardholder` — cardholder name
- `~/.cache/openclaw-gateway/visa_expiry` — expiry date (MM/YYYY)
- `~/.cache/openclaw-gateway/visa_type` — card type (visa)
- `~/.cache/openclaw-gateway/visa_billing_address` — full billing address
- `~/.cache/openclaw-gateway/visa_credit_limit` — spending cap set by Dylan

All card cache files are `chmod 600` (owner-only read/write).

## Notes

- The service account is **read-only** — cannot create or modify vault items
- The vault is named `OpenClaw` — only items in this vault are accessible
- Items in other vaults (`Private`, etc.) are NOT accessible
- If `op read` times out, fall back to cached values where available
- The `OP_SERVICE_ACCOUNT_TOKEN` is loaded from `~/.openclaw/.env-token`
