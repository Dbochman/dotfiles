# Ola messaging

Implementation notes and shareable feedback from the first attended setup are
recorded in [`OLA-FEEDBACK.md`](OLA-FEEDBACK.md).

OpenClaw uses Ola as a narrowly scoped messaging channel through three
interfaces:

- Ola's remote Streamable HTTP MCP server provides `get_inbox`,
  `get_messages`, and `send_message`;
- Ola sends a public `POST /hooks/wake` whose raw JSON body is authenticated
  with `X-Hub-Signature-256`; and
- a loopback bridge verifies that HMAC and relays a fixed, content-free wake to
  OpenClaw with its private `X-OpenClaw-Token` credential.

Ola messages are conversation input, not trusted authorization for local tools
or external side effects. The heartbeat may read and reply in Ola; sensitive
actions still require confirmation through a trusted owner channel.

The MCP definition includes the stable `OpenClaw-Codex-MCP/1.0` user agent.
Ola's CloudFront edge rejects Codex's otherwise headerless Streamable HTTP
client, while this explicit identifier lets the request reach Ola without
changing authentication or widening the exposed tool set.

## Why the bridge exists

Ola signs the raw callback body with HMAC-SHA256 and sends the digest as
`X-Hub-Signature-256: sha256=...`. OpenClaw 2026.6.10 authenticates every
`/hooks/*` request before reading its body and accepts only
`Authorization: Bearer` or `X-OpenClaw-Token`. Hook mappings cannot bridge the
formats because they run after authentication and do not receive the original
raw bytes.

`ola-webhook-bridge.py` therefore binds only to `127.0.0.1:18790`, verifies the
signature with a constant-time comparison over the unchanged bytes, and
requires the signed body to be a JSON object. It deliberately discards the
webhook's `text` instead of promoting external content into an OpenClaw system
event. A fixed local instruction is sent over a direct, proxy-free socket only
to `http://127.0.0.1:18789/hooks/wake`. The bridge also rejects other callback
paths, chunked or duplicate security headers, invalid signatures, and bodies
over 16 KiB; socket timeouts and a bounded worker pool limit public-ingress
resource use. It logs only bounded operational metadata, never the body,
signature, or credentials.

## Credentials

The protected mode-`0600` cache populated by the attended 1Password refresh
helper carries three independently rotatable values:

- `OLA_API_KEY` authenticates OpenClaw's MCP client to Ola;
- `OLA_WEBHOOK_SECRET` verifies Ola's public HMAC-signed callback; and
- `OLA_HOOK_TOKEN` authenticates the bridge's loopback request to OpenClaw.

The owner-only `~/.openclaw/.secrets-refresh.env` contains only exact
`OP_REF_OLA_API_KEY`, `OP_REF_OLA_WEBHOOK_SECRET`, and
`OP_REF_OLA_HOOK_TOKEN` field references. No value belongs in this repository,
command arguments, the LaunchAgent plist, or the tracked OpenClaw config.

The bridge wrapper sources the cache and launches a clean environment
containing only the two values it needs. It never invokes `op`. Rotating Ola's
webhook secret requires an attended cache refresh and a bridge restart, but no
OpenClaw restart. Rotating the private OpenClaw hook token requires refreshing
both the bridge and gateway processes so their in-memory values remain equal.

## Public callback

Keep the OpenClaw gateway loopback-bound and retain the existing tailnet-only
Serve route on port 443. A separate Tailscale Funnel exposes only the bridge's
wake path on allowed HTTPS port 10000:

```bash
tailscale funnel --bg --yes \
  --https=10000 \
  --set-path=/hooks/wake \
  http://127.0.0.1:18790/hooks/wake
```

The callback URL remains:

```text
https://<public-funnel-host>:10000/hooks/wake
```

Keep the concrete host in Ola's protected configuration rather than in this
public repository.

Do not switch `gateway.tailscale.mode` to `funnel`; that would publish the full
gateway surface. Confirm `tailscale funnel status --json` shows a single public
handler for `/hooks/wake` on port 10000 while private Serve remains on port 443.

Targeted rollback:

```bash
tailscale funnel --yes \
  --https=10000 \
  --set-path=/hooks/wake \
  http://127.0.0.1:18790/hooks/wake \
  off
```

## Attended enrollment and deployment

1. In Ola's agent settings, rotate the Webhook delivery shared secret and
   immediately store the one-time value either as the password of a dedicated
   item or as a separate concealed `webhook_secret` field. Never replace the
   MCP API key's password field or reuse the private OpenClaw hook token.
2. Add that exact field's reference as `OP_REF_OLA_WEBHOOK_SECRET` in the
   owner-only mode-`0600` refresh seed.
3. Before the first deployment, run the updated tracked helper directly with
   `~/dotfiles/openclaw/bin/openclaw-refresh-secrets --interactive`; older
   deployed copies predate `OLA_WEBHOOK_SECRET`. Confirm all three Ola keys are
   present by name without printing their values.
4. Deploy `ola-webhook-bridge.py` and its wrapper to `~/.openclaw/bin/`, copy
   `ai.openclaw.ola-webhook-bridge.plist` to `~/Library/LaunchAgents/`, and
   bootstrap the LaunchAgent.
5. Require `http://127.0.0.1:18790/healthz` to return HTTP 200, then retarget
   only the port-10000 Funnel path from port 18789 to port 18790.
6. If Ola shows its webhook circuit breaker as tripped, use the portal's reset
   control after the bridge is healthy.
7. Send a brand-new Ola message. Require the bridge log to record `accepted`,
   the main OpenClaw session to update, the Ola message to receive one reply,
   and Ola's delivery counter to increment. `openclaw system heartbeat last`
   may not update for an immediate hook-triggered run, so treat it only as an
   optional diagnostic rather than an acceptance gate.

No OpenClaw restart or upgrade is needed for this bridge rollout.

## Verification

1. Run `python3 -m unittest openclaw.tests.test_ola_integration
   openclaw.tests.test_ola_webhook_bridge`.
2. Run `plutil -lint
   openclaw/launchagents/ai.openclaw.ola-webhook-bridge.plist`.
3. Confirm unauthenticated, malformed, and incorrect-signature bridge requests
   fail closed without creating a heartbeat.
4. Confirm an authenticated local OpenClaw request still returns HTTP 200.
5. Send a real human message through Ola and confirm the signed callback causes
   an immediate heartbeat, which reads and replies using `human_id`.
6. Run `openclaw security audit` and inspect new hook or public-ingress
   findings.
