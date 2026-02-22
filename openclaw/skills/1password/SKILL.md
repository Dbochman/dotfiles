---
name: 1password
description: Read secrets, credentials, and payment information from the OpenClaw 1Password vault. Use when asked about passwords, API keys, credit card details, SSH keys, or any stored credentials. Also use when making purchases that require card information.
allowed-tools: Bash(1password:*)
metadata: {"openclaw":{"emoji":"🔐","requires":{"bins":["op"]}}}
---

# 1Password Vault Access

Read secrets and credentials from the OpenClaw vault via the `op` CLI. The service account has **read-only** access.

## Setup

The service account token is at `~/.openclaw/.env-token`. Set it before using `op`:

```bash
export OP_SERVICE_ACCOUNT_TOKEN=$(cat ~/.openclaw/.env-token)
```

## Reading Secrets

### Read a specific field
```bash
export OP_SERVICE_ACCOUNT_TOKEN=$(cat ~/.openclaw/.env-token)
op read "op://OpenClaw/Item Name/field"
```

### Common items in the vault

| Item | Field | Read Command |
|------|-------|-------------|
| OpenAI API Key | password | `op read "op://OpenClaw/OpenAI API Key/password"` |
| ElevenLabs API Key | password | `op read "op://OpenClaw/ElevenLabs API Key/password"` |
| Gateway Token | password | `op read "op://OpenClaw/OpenClaw Gateway Token/password"` |
| GOG CLI | password | `op read "op://OpenClaw/GOG CLI/password"` |
| Digital Ocean API | credential | `op read "op://OpenClaw/Digital Ocean API Credential/credential"` |
| Tavily | password | `op read "op://OpenClaw/Tavily/password"` |
| Google Places API | password | `op read "op://OpenClaw/Google Places API/password"` |
| OpenRouter | password | `op read "op://OpenClaw/OpenRouter/password"` |
| Resy | password | `op read "op://OpenClaw/Resy/password"` |
| OpenTable | password | `op read "op://OpenClaw/OpenTable/password"` |

### Credit Card (Visa)
```bash
export OP_SERVICE_ACCOUNT_TOKEN=$(cat ~/.openclaw/.env-token)

op read "op://OpenClaw/Visa/number"                # Card number
op read "op://OpenClaw/Visa/expiry date"            # Expiration (YYYYMM)
op read "op://OpenClaw/Visa/verification number"    # CVV
op read "op://OpenClaw/Visa/cardholder name"        # Cardholder
op read "op://OpenClaw/Visa/credit limit"           # Spending cap
op read "op://OpenClaw/Visa/address"                # Billing address
```

### List all items
```bash
export OP_SERVICE_ACCOUNT_TOKEN=$(cat ~/.openclaw/.env-token)
op item list --vault OpenClaw --format json
```

### Get full item details
```bash
export OP_SERVICE_ACCOUNT_TOKEN=$(cat ~/.openclaw/.env-token)
op item get "Item Name" --vault OpenClaw --format json
```

## Using Credit Card for Purchases

When making online purchases with the credit card:

1. **Read card details** from 1Password (never from files)
2. **Use browser automation** to navigate to the checkout page
3. **Fill in payment fields** with the card data
4. **Always confirm with Dylan** before completing any purchase
5. **Never log or display full card numbers** — show only last 4 digits

### Purchase Safety Rules

- ALWAYS ask Dylan for confirmation before submitting any payment
- NEVER share card details in messages, logs, or chat
- When displaying card info to Dylan, mask it: `****-****-****-0298`
- If a purchase requires 2FA or SMS verification, notify Dylan
- Check credit limit before purchasing
- NEVER exceed the credit limit — this is a hard spending cap set by Dylan
- Flag anything over $100 for explicit approval even if under the limit

## Notes

- The service account is **read-only** — cannot create or modify vault items
- The vault is named `OpenClaw` — only items in this vault are accessible
- Items in other vaults (`Private`, etc.) are NOT accessible
- The `OP_SERVICE_ACCOUNT_TOKEN` is loaded from `~/.openclaw/.env-token`

## Secret Caching (Gateway Only)

The gateway wrapper uses a **cached secrets file** instead of calling `op read` at startup.
This is a necessary exception to the "read from 1Password at runtime" principle.

### Why caching is required

On macOS Tahoe (26.x), `op read` **hangs indefinitely** when called from a LaunchAgent
context. The 1Password CLI spawns an `op daemon --background` process that attempts to
connect to the 1Password desktop app via a Mach bootstrap service. Under launchd, this
connection blocks forever because the desktop app requires user interaction (Touch ID/GUI
prompt) that can't happen in a non-GUI launchd context.

Every approach was tested and failed:
- `OP_SERVICE_ACCOUNT_TOKEN` alone — daemon still spawns before token is evaluated
- `OP_BIOMETRIC_UNLOCK_ENABLED=false` — Mach port connection precedes env check
- `--config /isolated/dir` — new daemon spawns, same behavior
- `env -i` minimal environment — Mach ports are per-user-session, not env-based
- Background process + kill timer — works but each `op` invocation also triggers
  incessant TCC "op would like to access data" popups on Tahoe that don't persist

The **only** reliable solution is to never call `op` from the launchd wrapper at all.

### How it works

- **Cache file**: `~/.openclaw/.secrets-cache` (chmod 600, KEY=VALUE format)
- **Gateway wrapper** sources the cache via `set -a; source $CACHE; set +a`
- **Refresh helper**: `~/bin/openclaw-refresh-secrets` — fetches secrets from 1Password
  via `op read` and writes them to the cache. Run over SSH after key rotation.
- `op read` works fine over SSH and in interactive terminals — only launchd is broken.

### Security posture

- Cache file is `chmod 600` (owner-only read/write)
- File lives outside the dotfiles repo (not committed to git)
- Refresh is manual — secrets only update when explicitly requested
- After rotating any API key in 1Password, you must SSH into the Mini and run:
  `~/bin/openclaw-refresh-secrets`
- The agent itself still uses `op read` at runtime for on-demand secret access
  (this works because the agent runs inside the gateway's node process, which
  already has the secrets loaded as environment variables)
