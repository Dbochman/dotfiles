# Ola + OpenClaw setup feedback

Date: 2026-07-22

## Summary

The end result is good: a new Ola message now causes an immediate signed
callback, wakes OpenClaw, performs a bounded inbox read, and produces one reply.
The MCP surface is pleasantly small, and HMAC-signing the public webhook is the
right security model.

The difficult part was getting from "Connected" to a verified end-to-end
message. Two compatibility issues and some ambiguous credential language made
the setup look healthier than it was. We ultimately needed an explicit MCP
`User-Agent` and a local authentication bridge for our existing OpenClaw
release. Clearer preflight checks and delivery diagnostics would have made both
issues visible much earlier.

## What worked well

- The three-tool MCP contract (`get_inbox`, `get_messages`, and `send_message`)
  is easy to reason about and constrain.
- The instructions correctly distinguish the inbox entry's `human_id` from the
  conversation `grant_id`; that prevented an easy reply-routing mistake.
- The callback arrived immediately once authentication was compatible. The
  agent did not have to wait for its normal heartbeat interval.
- Signing the unchanged request body with HMAC-SHA256 provides a clean public
  ingress boundary.
- The webhook delivery counters and last-failure timestamp gave us a useful
  starting point, even though more detail was needed.

## Friction we encountered

### 1. Three credentials looked like one setup token

The integration actually has three independent credentials:

1. the Ola API key used by the MCP client;
2. the Ola webhook signing secret used to verify public callbacks; and
3. the private OpenClaw hook token used by OpenClaw's `/hooks/wake` endpoint.

The install snippet embeds the API key directly, the OpenClaw step asks the user
to "pick a token," and the webhook settings later expose another shared secret.
That wording made it easy to overwrite or reuse the wrong value. We briefly
replaced the stored API key while trying to rotate the webhook secret and had to
repair the credential item.

Suggested improvements:

- Name all three credentials together before showing any commands.
- Explicitly say that they must be distinct and which component consumes each
  one.
- Label the UI value "Webhook HMAC signing secret," not just "shared secret."
- Warn beside rotation that the value is shown once and must not replace the
  agent API key.
- Generate commands with environment-variable placeholders by default so a
  bearer key is not copied into shell history or screenshots.

### 2. The MCP endpoint required a non-default `User-Agent`

Authentication was valid, but the Streamable HTTP client could not reliably
reach the Ola MCP endpoint until we added an explicit `User-Agent`. The failure
looked like a credential or transport problem even though it occurred at the
edge before normal MCP handling.

Suggested improvements:

- Include a known-good `User-Agent` in the generated OpenClaw command.
- Add an MCP preflight in the UI or CLI that distinguishes edge rejection,
  authentication failure, and protocol failure.
- Publish a tested OpenClaw version matrix and the exact required server
  configuration for each supported version.

### 3. Ola's HMAC callback was incompatible with our OpenClaw hook auth

Our installed OpenClaw 2026.6.10 authenticates `/hooks/*` with
`Authorization: Bearer` or `X-OpenClaw-Token` before it reads the body. Ola sends
an HMAC digest of the raw body in `X-Hub-Signature-256`. OpenClaw hook mappings
therefore could not translate between the two schemes: mappings run too late
and do not receive the original raw bytes.

The callback consequently returned `401` even though the URL was public and
correct. We solved this without exposing the gateway by adding a loopback-only
bridge that verifies Ola's HMAC, discards the untrusted envelope, and forwards a
fixed wake using the separate private OpenClaw token.

Suggested improvements:

- Detect the target OpenClaw version before declaring setup complete.
- If a minimum OpenClaw version supports Ola's signature directly, state and
  enforce that minimum. Otherwise provide a maintained adapter or an officially
  documented reverse-proxy recipe.
- Make the generated setup use the webhook signing secret for HMAC verification
  and the OpenClaw hook token for the private hop; do not imply that one token
  can satisfy both protocols.
- Add a signed "Send test callback" action that exercises the exact production
  headers and body.

### 4. "Connected" did not mean webhook delivery was healthy

The agent showed `Connection status: Connected` while webhook delivery showed
zero successes and repeated failures. In practice, "Connected" appeared to
mean the Ola agent/API registration was valid, not that automatic wake delivery
worked.

Suggested improvements:

- Show separate health states for MCP access and webhook delivery.
- Reserve an overall green "Connected" state for an end-to-end check, or label
  it more narrowly as "Agent registered."
- Surface the last callback's HTTP status, a safe response summary, a request
  ID, retry state, and whether the webhook circuit breaker is open.
- Put the reset/retry control next to the failure state and explain when it is
  needed.

### 5. Wake timing was easy to misunderstand

The setup says Ola triggers a heartbeat, while OpenClaw also has a periodic
heartbeat cadence. That left us unsure whether a new message should be answered
immediately or only on the next scheduled heartbeat. Manual inbox checks worked
while automatic delivery did not, which added to the ambiguity.

Suggested improvements:

- Say explicitly: "A successful webhook requests an immediate heartbeat; the
  scheduled heartbeat is only a fallback."
- Show a short event timeline in the test UI: callback sent, callback accepted,
  heartbeat requested, inbox read, and reply sent.
- Recommend testing with a brand-new message after setup, because an already
  read thread does not prove automatic wake delivery.

### 6. The broad tunnel example is convenient but wider than necessary

Publishing the entire OpenClaw gateway through a tunnel is a large exposure for
an integration that needs one POST path. We kept the gateway loopback-bound and
used a separate Tailscale Funnel route that exposes only `/hooks/wake` to the
HMAC-verifying bridge.

Suggested improvements:

- Lead with a path-scoped reverse proxy or tunnel example.
- Clearly mark full-gateway publication as the higher-risk option.
- Include an external unsigned probe that should return `401`, followed by the
  signed test callback that should return `200`.

### 7. The optional consent installer exposes the API key in a command

The consent plug-in example correctly places `OLA_API_KEY=...` on the `bash`
side of the pipe, but it still asks the user to paste a live bearer credential
into a shell command. That value can remain in shell history or copied terminal
transcripts.

Suggested improvements:

- Accept the key over standard input, from an already populated environment
  variable, or through a documented secret-manager reference.
- Keep the consent plug-in clearly labeled as optional and separate from the
  messaging/webhook acceptance test.

## Environment-specific friction that was not an Ola defect

Some of the work belonged to our deployment and should not be attributed to
Ola:

- We intentionally kept OpenClaw 2026.6.10 running without an upgrade or
  restart, which made a compatibility bridge preferable to changing the
  gateway during this session.
- Our credentials are materialized through an attended exact-field 1Password
  workflow, so adding and correcting the three values required local cache
  maintenance.
- Our gateway is deliberately loopback-only, so we chose a path-scoped
  Tailscale Funnel instead of publishing the full gateway.
- One confusing terminal interaction came from our own refresh helper waiting
  for an exact confirmation word.

## Suggested setup acceptance test

The installer should not call the integration complete until all of these pass:

1. MCP authentication succeeds with the generated headers.
2. `get_inbox` succeeds without exposing message content in setup logs.
3. The public callback URL is reachable from outside the user's authenticated
   network.
4. An unsigned callback is rejected.
5. Ola's signed test callback is accepted and requests an immediate wake.
6. A brand-new human message is read and receives exactly one reply.
7. The UI reports MCP and webhook health separately.

## Proven end state

Our working deployment keeps the OpenClaw gateway private, exposes only the
callback path, verifies Ola's raw-body signature with a dedicated secret, and
forwards a content-free wake with a different private token. The agent then
uses only the three allowlisted Ola tools for its inbox routine. A real message
successfully exercised the full path from Ola delivery through the OpenClaw
reply.

That outcome makes the integration feel worthwhile. Most of the setup burden
could be removed with clearer credential naming, an OpenClaw compatibility
preflight, and a first-class signed callback test.
