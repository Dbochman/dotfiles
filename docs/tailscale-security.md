# Tailscale security operations

Verified October 25, 2026. This runbook records the deployed network boundary;
it is not a deployable policy. Exact addresses, account identities, policy
files, device inventory, and probe results remain in protected local state.

## Applied controls

- **Funnel is disabled by policy.** The member-wide `funnel` node attribute was
  removed. No Funnel grant remains in the saved policy, and the Mini no longer
  advertises the Funnel capability or permitted Funnel ports.
- **Ola public wake is retired.** Its HTTPS-10000 route was removed and its
  bridge was unloaded and persistently disabled. The subsequent full retirement
  removed the MCP connection, heartbeat routine, local hook credentials, and
  installed bridge files. The owner confirmed provider revocation. Private
  Serve routes remain intact. See [Ola retirement](../openclaw/OLA.md).
- **EchoNest cannot initiate personal-tailnet connections.** The all-source
  network grant was replaced with `ipset:trusted-existing-devices`, containing
  the IPv4 and IPv6 addresses of the other twelve enrolled devices. Their
  existing network access, including retained work-device and household
  administration access, is preserved. EchoNest can respond to permitted
  incoming connections; its public Internet service is separate from this rule.
- **Only EchoNest has `tag:echonest`.** The tag replaces its user identity,
  closing same-owner Taildrop access that network grants alone do not block.
  Existing Mac identities remain unchanged because automation uses Taildrop.
- **Administrative SSH is explicit.** The existing check-mode self rule is
  retained, with a separate owner check-mode rule for `tag:echonest`, including
  root. The tag is assignable by tailnet administrators.
- **Tailnet Lock remains enabled.** User approval and automatic client updates
  were already enabled and were not changed by this rollout.

The public service must not be added to the trusted source set to repair an
unrelated outage. Investigate the dependency and review a narrow exception if
one is actually needed. Do not restore wildcard source access as routine repair.

## Verification evidence

The rebased policy passed Tailscale validation with 57 network cases covering
2,912 accept/deny assertions and two SSH cases. The recovery policy also passed
validation. The owner saved the policy in the admin console and assigned the
tag; the audit credential remained read-only.

After application:

1. An API reread exactly matched the validated policy. EchoNest had the tag;
   no other device was tagged. No Funnel grant was restored.
2. Fresh SSH from the trusted workstation into EchoNest succeeded.
3. Five TCP probes from EchoNest to personal-host SSH and the Mini finance
   service failed to connect, across IPv4 and IPv6. All five succeeded before
   the change and still succeeded from the trusted workstation afterward.
4. The application and Redis containers remained healthy; the player remained
   running. Public ports 80 and 443 accepted TCP connections. This was not a
   full application or playback regression test.
5. The Mini's private Serve mappings matched their saved configuration, and
   Tailnet Lock remained enabled.

The live probes sample important destinations; the policy regression cases
cover the broader device inventory, subnet destinations, and protocols. Neither
is a blanket clearance of every application or every public network path.

## Enrollment and changes

The trusted source set is an explicit inventory, not automatic membership.
New or re-enrolled devices may have no initiated access until their current
addresses are reviewed and added. A signing approval alone does not add a
device to this network grant.

For a device replacement or enrollment:

1. Confirm its identity, owner, intended role, and current IPv4/IPv6 addresses.
2. Complete the existing Tailnet Lock enrollment requirements. Keep working
   access through another trusted administrator device.
3. Review whether the new device should receive broad trusted access or a
   narrower grant. Remove obsolete address entries only after checking their
   consumers; do not include EchoNest or another public server by default.
4. Update policy regression cases and validate before saving. Retest fresh
   administrative access and EchoNest's denied outbound probes afterward.

Changes to the live policy require a fresh read. Use revision/ETag protection
when applying through an API, or compare the current editor content before
saving through the console. Never broaden the read-only audit credential as
an incidental step. See [SSH host access](ssh-host-access.md) for transport and
host-key handling; ordinary OpenSSH authentication is distinct from Tailscale
SSH's check-mode authorization.

## Recovery

The October rollout's protected evidence is under
`~/.local/state/tailscale-hardening/20261024-223551/` on the Mini. The operator
workstation also holds the staged files under
`~/.local/state/tailscale-hardening/20261025-echonest/`.

- `echonest-isolation-proposed.json`: the validated, applied policy.
- `echonest-isolation-rollback.json`: validated emergency recovery policy.
- `echonest-isolation-completion.json`: results of the live checks on the Mini.

Recovery restores broad network access temporarily while preserving explicit
SSH access to the tagged server and keeping Funnel disabled. Review its diff
against the current live policy before use; later legitimate changes must not
be overwritten. Revalidate, save through the admin console, and retest access.
Then investigate and restore isolation.

Do not use the earlier `policy-rollout-*` drafts: they predate Funnel removal.
Do not restore the original self-only SSH policy after tagging EchoNest; it
does not preserve the explicit tagged-server authorization. Removing the final
tag can require attended reauthentication and is not the primary rollback.

## Remaining work and boundaries

- Other trusted devices retain broad mutual access by owner choice. This is
  isolation of the public server, not complete segmentation of the household.
- The last settings audit recorded a 36-hour admin-console inactivity timeout
  and disabled key expiry on eleven devices. The proposed shorter console
  timeout and selective portable-device expiry have **not** been applied.
- Client updates, administrator roles, identity-provider MFA/recovery, and
  Tailnet Lock recovery-material custody remain separate follow-ups. An update
  availability flag alone does not establish a vulnerability.
- Tailscale grants do not filter direct LAN or public-interface traffic. The
  Mini's router reported CGNAT IPv4 and globally addressed IPv6. UPnP discovery
  did not yield a mapping inventory, router firewall inspection was denied,
  and external inbound IPv6 verification lacked a working external IPv6
  vantage. Host-firewall findings and exact addresses remain in private audit
  state. Do not describe these paths as verified closed.
- Optional integrations that need Funnel, including the manual Plaid Payroll
  Income Link flow, require a separately approved temporary exception. Routine
  maintenance must not restore the removed member-wide capability.

References: [Funnel](https://tailscale.com/docs/features/tailscale-funnel),
[grants](https://tailscale.com/docs/features/access-control/grants),
[tags](https://tailscale.com/docs/features/tags),
[Taildrop](https://tailscale.com/docs/features/taildrop), and
[Tailnet Lock](https://tailscale.com/docs/features/tailnet-lock).
