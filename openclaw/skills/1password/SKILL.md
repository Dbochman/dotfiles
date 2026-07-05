---
name: 1password
description: Safely use exact 1Password fields during attended interactive work and maintain the protected OpenClaw secrets cache. Use for passwords, API keys, credentials, payment data, cache refreshes, or diagnosing credential access. Never use `op` from a LaunchAgent or any child process it starts.
allowed-tools: Bash(openclaw-refresh-secrets:*)
metadata: {"openclaw":{"emoji":"🔐","requires":{"bins":["op"]}}}
---

# 1Password and cached secrets

Choose one of two modes before accessing credentials. They are deliberately
separate because macOS LaunchAgents cannot safely interact with the 1Password
desktop session.

## Mode 1: attended interactive access

Use this mode only in a user-attended terminal for an immediate task. The
agent does not have direct `op` tool permission; ask the owner to run the exact
single-field read in their terminal when a one-off field is needed.

1. Identify the single field the task needs.
2. Obtain its exact `op://` reference from an owner-only local configuration or
   the user. Do not place vault, item, field, or account identifiers in tracked
   files merely for convenience.
3. Run `op read "$FIELD_REFERENCE"` and capture the result directly into the
   consuming command or a short-lived variable.
4. Do not print, inspect, summarize, log, or return the value. Unset temporary
   variables when the command finishes.

Example pattern using a non-secret placeholder reference:

```bash
secret_value=$(op read "$FIELD_REFERENCE") || exit 1
SERVICE_TOKEN="$secret_value" command-that-needs-it
unset secret_value
```

Read exact fields only. Do not use full-item JSON, item lists, note blobs, bulk
exports, or commands that reveal unrelated fields.

## Mode 2: LaunchAgent and unattended services

LaunchAgents and every child process they start must be cache-only:

```bash
CACHE="${OPENCLAW_SECRETS_CACHE:-$HOME/.openclaw/.secrets-cache}"
[[ -f "$CACHE" && ! -L "$CACHE" ]] || exit 1
set -a
source "$CACHE"
set +a
[[ -n "${REQUIRED_KEY:-}" ]] || exit 1
exec service-command
```

- Never run `op`, probe it with a timeout, start its daemon, or set a service
  account token as a workaround.
- Audit indirect dependencies too. A CLI that falls back to `op` is not safe
  for LaunchAgent use until that fallback is disabled or made interactive-only.
- Fail closed when the cache or a required key is absent. Preserve the last
  known-good service state rather than replacing cache data with blanks.
- Do not print or `cat` the cache during diagnosis.

## Refreshing the protected cache

Run the tracked helper manually after a vault rotation or when adding a cache
key:

```bash
openclaw-refresh-secrets --interactive
```

The helper reads exact field references from
`~/.openclaw/.secrets-refresh.env` by default. That owner-only mode-`0600` file
is local input and is never committed. It may contain:

- `OP_REF_<CACHE_KEY>` assignments for exact 1Password fields;
- private cache-only identity, device, account, and household values that do
  not yet have configured exact vault-field references; and
- optional Mysa references as a complete username/password pair.

Current required local-seed/cache keys are grouped by meaning:

- general private identities: `DYLAN_EMAIL`, `JULIA_EMAIL`;
- service-specific identities: `OPENCLAW_EMAIL`, `OPENTABLE_EMAIL`,
  `ECHONEST_EMAIL`, `STARMARKET_GMAIL`, `TRYFI_EMAIL`;
- credentials or authentication material: `STARMARKET_DEVICE_TOKEN`,
  `EIGHTSLEEP_CLIENT_SECRET`, `CIELO_API_KEY`, `TRYFI_PASSWORD`;
- private device/account identifiers: `STARMARKET_USER_HASH`,
  `EIGHTSLEEP_CLIENT_ID`, `EIGHTSLEEP_DYLAN_USER_ID`,
  `EIGHTSLEEP_JULIA_USER_ID`, `EIGHTSLEEP_CROSSTOWN_DEVICE_ID`,
  `EIGHTSLEEP_CABIN_DEVICE_ID`, `PETLIBRO_APPSN`, `HOUSEHOLD_CHAT_ID`,
  `JULIA_CHAT_ID`, `DYLAN_CHAT_ID`; and
- private household location data: `CROSSTOWN_LAT`, `CROSSTOWN_LON`,
  `CABIN_LAT`, `CABIN_LON`.

An identifier is not automatically a secret, but these identities, device
identifiers, and locations are still private and must remain in the owner-only
seed/cache. Truly public, non-household configuration may remain tracked and
does not belong in this cache. Use the general identity keys for GWS and contact
identity; use service-specific keys only for the named service.

Vault-backed values require an `OP_REF_<CACHE_KEY>` exact-field reference in
the seed. This includes `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`,
`OPENCLAW_GATEWAY_TOKEN`, the Cielo and Star Market username/password pairs,
the Plaid client and environment secrets, and all four `NEST_*` fields.

The helper:

- refuses to run without `--interactive`, a real terminal, and the owner
  typing the displayed confirmation word;
- accepts only regular owner-owned mode-`0600` cache and seed files;
- calls only `op read` for configured exact fields;
- never prints field references or values;
- requires every configured field before changing the cache;
- shell-quotes every value, writes a mode-`0600` temporary file, fsyncs it, and
  atomically renames it over the cache; and
- leaves the previous cache byte-for-byte intact on any missing field or error.

Restart only services that need rotated values after a successful refresh.

## Payment information

Treat payment fields as highly sensitive interactive-only data.

- Retrieve only the specific fields required by an explicitly authorized
  checkout.
- Inject values directly into the approved checkout; never display them in
  chat, terminal output, screenshots, logs, or saved automation artifacts.
- Show the user the merchant, item, quantity, delivery details, recurring
  terms, and final total, then obtain explicit confirmation before submission.
- Stop for MFA, a changed total, an unexpected subscription, or an unfamiliar
  payment/cancellation term.
- Never add payment fields to `.secrets-cache` or the refresh seed.

## Diagnosis

When a service reports missing credentials:

1. Determine whether it is interactive or LaunchAgent-owned.
2. For LaunchAgents, inspect only cache presence, ownership, mode, required key
   names, and child command paths. Never test by invoking `op`.
3. For interactive work, verify `op` authentication with a non-revealing status
   command, then retry one exact-field read without printing its result.
4. Run `openclaw-refresh-secrets --interactive` when the cache needs repair.

The repeated Tahoe GUI prompt/hang is resolved by removing `op` from the entire
LaunchAgent process tree, not by adding `OP_SERVICE_ACCOUNT_TOKEN`.
