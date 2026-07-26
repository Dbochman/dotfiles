# Crosstown Strict Presence Scanner Canary

## Status

ACTIVE — the strict Crosstown scanner was activated on `2026-07-26` at reviewed
source hash
`3c795e93244d0624dec426f350b93e7af1b6e4652e6d4b095e3b8f1fdb2470cf`.
The MacBook Pro approval is pinned to that exact hash, and the exact prior
legacy scanner is retained in an owner-only rollback bundle.

Preliminary progress at `2026-07-20T11:02:59Z`: binding parity and strict
config validation passed; a same-window shadow comparison had sanitized
output, unchanged canonical state, zero decision mismatches, and at most two
minutes of timestamp skew. One comparison against an older scheduled snapshot
was discarded because it differed by one boolean outside the accepted
comparison window. The later same-window check showed parity but did not
explain or erase that earlier mismatch, so it is not counted as passing
evidence.

At `2026-07-20T11:41:02Z`, `presence-crosstown-canary` completed the first
tracked real-cadence acceptance sample for strict source hash
`d9344a286c718b049c40d665013e9c6ff224274c6048812b8bd4bb19303d1482`.
Config and binding parity were true, strict output was sanitized, canonical
state was unchanged, the mismatch count was zero, and timestamp skew was at
most 30 seconds. This is sample 1 of 4; three real-cadence samples and the
remaining scenario evidence are still required. Both site-local approval
files remain absent.

At `2026-07-20T11:32:31Z`, the one-time updater bootstrap below completed from
clean commit `a8c945f`: the tracked and runtime updater hashes both verified as
`0bb02acb6fab03727ea41baa70454608757aa4d77e6a92a73cc1adaf6603c6c0`, and the
runtime gate markers were present. No scanner was deployed; both sites still
had the same preserved legacy scanner hash and neither approval file existed.

On `2026-07-26`, the current reviewed hash passed strict config, binding,
scanner, receiver, correlation, home-event, vacancy-action, deployment, shell,
and Python preflight tests. Attended Wi-Fi off/on checks independently removed
and restored each phone. The first immediate Julia-off comparison was
discarded on a decision mismatch; the later aged observation passed with zero
mismatches, as did both return observations. Both phones were then positively
observed together. Dylan explicitly waived the remaining hour-cadence soak and
locked/sleeping-phone sample before authorizing activation.

Activation followed the guarded production sequence below. All downstream
consumers were stopped, the legacy runtime was copied into the protected
rollback bundle, and the exact approval was created before the normal dotfiles
deployment. Deployed hash/config/observation checks passed. A strict production
scan landed with both residents present while consumers remained stopped; the
receiver then advanced canonical state with Crosstown occupied, Cabin confirmed
vacant, and zero transitions. Vacancy actions and the Nest reviewer were
restored in order. A forced launch-agent tick subsequently traversed the normal
scheduled path and again produced zero transitions. Final bus status was
healthy with zero pending or leased deliveries, dead letters, or ready spool
files; the sole open incident was the expected shadowed occupied-activity
observation.

## One-time updater bootstrap

The Mini's deployed updater predates this approval gate. Its old process keeps
running its original inode after a pull, so a combined first rollout could
otherwise validate and copy the strict Crosstown scanner before self-update.
Do not invoke that old updater for this rollout.

From a clean committed checkout, first syntax-check the tracked
`dotfiles-pull.command`, copy it to an owner-only temporary file beside the
runtime updater, verify the temporary and source SHA-256 values match, chmod it
`0755`, and atomically replace `~/.openclaw/bin/dotfiles-pull.command`. Then
require the runtime hash to equal the source hash and quietly verify that the
runtime contains the `presence-scanner-approved.sha256` gate. The matching
tracked `openclaw/lib/deployment.sh` must be present in that same clean
checkout before the newly bootstrapped updater is run.

This is a one-time control-plane bootstrap, not scanner activation. Both
site-local approval files must still be absent, and both deployed scanner
hashes must remain on the legacy value until their separate canaries pass.

## Objective

Replace Crosstown's legacy exact-MAC-plus-hostname-fallback scanner with the
tracked strict scanner, which accepts only the two protected exact MAC bindings
and emits no device names, IP addresses, MAC addresses, or raw ARP rows.

The canary must not write canonical presence, send Taildrop, publish a home
event, trigger vacancy actions, affect camera review, or expose either the
legacy environment binding or raw scanner output.

## Activation boundary

Routine deployment requires all three conditions on the MacBook Pro:

1. The tracked scanner advertises `strict-site-bindings-v1`.
2. `~/.openclaw/presence-devices.json` passes strict validation.
3. The owner-only mode-`0600`
   `~/.openclaw/presence-scanner-approved.sha256` contains exactly the current
   tracked scanner SHA-256 plus one newline.

The approval file must remain absent until the complete shadow gate passes. A
different future source hash automatically returns deployment to pending until
that exact candidate is reviewed again.

## Privacy-safe sample contract

Each shadow sample runs entirely on the Crosstown MacBook Pro:

- Stream the reviewed strict scanner bytes over SSH; never install them.
- Set `HOME` to an empty temporary directory,
  `PRESENCE_DEVICE_CONFIG` to the real protected JSON,
  `PRESENCE_TAILSCALE_BIN=/usr/bin/false`, and
  `HOME_EVENTS_PRESENCE_ENABLED=0`.
- Run strict `validate-config crosstown`, then strict `observe crosstown`.
- Read the current protected legacy canonical snapshot only inside the remote
  process. Parse it in memory and reduce it to the two resident booleans before
  anything crosses SSH; the underlying legacy artifact may contain private
  network identifiers. Require that snapshot to be no more than three minutes
  old so each sample stays aligned with a real scheduled tick.
- Parse the legacy mode-`0600` environment file as bounded data, never source
  it, and compare its two exact bindings with the strict JSON in memory.
- Fingerprint the protected canonical scan artifact before and after. Discard
  the sample if a scheduled production tick races or any file metadata changes.
- Keep candidate bytes and captured child output only in bounded,
  process-scoped memory and release them when the helper exits; the helper
  creates no remote scanner file.

Run one sample from the Mini near the real scheduled Crosstown tick:

```bash
~/.openclaw/bin/presence-crosstown-canary
```

The helper has no scheduler, install path, or approval writer. The only
permitted result is a safe summary shaped like:

```json
{
  "config_valid": true,
  "binding_parity": true,
  "legacy_snapshot_fresh": true,
  "strict_output_sanitized": true,
  "canonical_unchanged": true,
  "decision_parity": true,
  "mismatch_count": 0,
  "ok": true,
  "scanner_sha256": "<reviewed 64-character source hash>",
  "site": "crosstown",
  "timestamp_skew_bucket": "le_2m"
}
```

No raw stderr, provider row, config value, address, name, command output, or
temporary path may be returned or logged.

## Shadow acceptance gate

Require all of the following before production activation:

- Scanner, strict observation, Crosstown matching, approval-contract, presence
  correlation, receiver, home-event, vacancy, and deployment tests pass.
- Strict JSON validation and legacy-to-JSON binding parity both pass.
- Four clean samples occur at the real 15-minute cadence over about one hour.
- Every sample reports zero mismatch and unchanged canonical state.
- Both bound phones are positively observed at least once, including a
  locked/sleeping interval.
- Perform one attended Wi-Fi off/on cycle per phone if practical; the strict
  identity must disappear and return without another client satisfying it.
- Any ambiguous ground truth, stale artifact, malformed observation, gateway
  liveness failure, or race resets the activation decision.

## Production activation

1. Record the exact reviewed source hash and verify the source remains clean.
2. Stop the Mini's presence receiver, vacancy actions, and Nest activity
   reviewer so a first strict Crosstown tick cannot trigger downstream work.
3. Atomically create the approval file on the MacBook Pro with the reviewed
   source hash, mode `0600`, and no extra content.
4. Run the normal dotfiles deployment. Require its Crosstown sync to report
   success, then compare the deployed and approved hashes.
5. Run deployed `validate-config crosstown` and sanitized `observe crosstown`.
6. Keep downstream consumers stopped for the scheduled strict canary. Inspect
   only safe booleans, freshness, and file metadata; stop on any mismatch.
7. Restore the receiver first. After one correct canonical evaluation, restore
   vacancy actions. Restore the Nest reviewer last after confirming no stale
   camera event can be reviewed.

No gateway restart is required.

## Rollback

1. Stop the Crosstown scanner and Mini downstream consumers.
2. Move the approval file into an owner-only rollback bundle so routine pulls
   cannot reinstall the strict candidate.
3. Restore the exact pre-canary legacy scanner hash on the MacBook Pro.
4. Validate the newest safe presence artifact before restoring the receiver,
   vacancy actions, and reviewer in that order.
5. Preserve safe reports and hashes; never copy or print either binding file.
