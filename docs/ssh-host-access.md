# Host-to-host SSH access

This document records the private SSH topology used by Dylan's workstation,
the OpenClaw Mac mini, the always-on Crosstown MacBook Pro, and Reachy Mini. It
describes the deployed state as verified on July 16, 2026. Private keys,
`authorized_keys`, `known_hosts`, and machine-specific overrides remain local to
their hosts and must never be committed.

## Hosts

| Role | SSH endpoint | Account | Network |
| --- | --- | --- | --- |
| Dylan's workstation | `dylans-mac` | `dylanbochman` | Tailscale |
| OpenClaw Mac mini | `dylans-mac-mini` / `100.104.114.1` | `dbochman` | Tailscale and Cabin LAN |
| Crosstown MBP | `dylans-macbook-pro` / `100.107.209.85` | `dbochman` | Tailscale and `192.168.165.0/24` |
| Reachy Mini | `192.168.165.129` | `pollen` | Crosstown LAN |

The workstation and Crosstown MBP may both report the local hostname
`Mac.lan`. Do not use that ambiguous Bonjour name for automation. Use the
tracked aliases, exact Tailscale IPs, or Reachy's exact LAN IP shown above.

## Access matrix

| From | To | Authentication | Purpose |
| --- | --- | --- | --- |
| Dylan's workstation | Crosstown MBP | 1Password-agent ED25519 key authorized on the MBP | Direct attended administration without the Mac mini jump host |
| Dylan's workstation | Mac mini | Existing `dylans-mac-mini` policy; dedicated local key when configured, otherwise the 1Password agent | OpenClaw administration |
| Mac mini | Crosstown MBP | `~/.ssh/id_mini_to_mbp`, selected for `dylans-macbook-pro` with `IdentityAgent none` | Reachy relay control, Crosstown presence, August, Roomba, and LAN operations |
| Crosstown MBP | Mac mini | MBP `~/.ssh/id_rsa` (an ED25519 key despite its legacy filename) to `dbochman@100.104.114.1` | Persistent loopback gateway upstream |
| Crosstown MBP | Reachy | `~/.ssh/openclaw-reachy` to `pollen@192.168.165.129` | Primary `reachyctl` and reverse gateway relay |
| Mac mini | Reachy | The same dedicated `~/.ssh/openclaw-reachy` identity over the existing Crosstown subnet route | Legacy gateway and direct-control rollback |

The Mac mini remains a valid recovery jump host, but it is no longer required
for an attended workstation-to-MBP shell. The direct path is:

```bash
ssh dbochman@100.107.209.85
# The tracked alias is equivalent when the local identity policy is present:
ssh dylans-macbook-pro
```

If direct access is unavailable, use the verified nested recovery path:

```bash
ssh dylans-mac-mini 'ssh -o BatchMode=yes dylans-macbook-pro "hostname; id -un"'
```

## Changes made for the Reachy migration

1. Confirmed that Remote Login was already enabled for `dbochman` on the
   Crosstown MBP.
2. Added the workstation's existing agent-backed public key to the MBP's
   owner-only `~/.ssh/authorized_keys`, preserving all existing entries.
3. Authorized the MBP's existing `~/.ssh/id_rsa.pub` on the Mac mini. This
   created the reverse MBP-to-Mini path needed for the persistent gateway
   upstream.
4. Verified the Mac mini host key independently, then pinned its numeric
   Tailscale endpoint in the MBP's `known_hosts` before allowing strict
   noninteractive SSH.
5. Securely copied the dedicated `openclaw-reachy` key pair from the Mac mini
   to the MBP, retained mode `0600`, and verified that both copies have the same
   public-key fingerprint.
6. Verified Reachy's host key from both the Mac mini and MBP before pinning it
   on the MBP.
7. Kept the existing Mini-to-MBP `id_mini_to_mbp` path unchanged for OpenClaw
   and other Crosstown automation.

No private key or `authorized_keys` content was added to Git. Only host aliases,
key locations, operational fingerprints, and services that consume them are
documented.

## Identity fingerprints

Fingerprints identify keys without publishing private material. The deployed
state is:

| Identity | SHA256 fingerprint |
| --- | --- |
| Workstation agent key authorized on Crosstown MBP | `SHA256:BYtX+JUe/NAY1YDGImvODV/X0uJwemNmbV4K0un5IkU` |
| Mac mini `id_mini_to_mbp` | `SHA256:BRLlK2OWzu+UpcPqg2JURcAcnchd0IcaFOG/22dpdNw` |
| Crosstown MBP `id_rsa` | `SHA256:AZ9qqvjaUwLBT6gi0DEkvueE03m7LTV6gLCttHhCt/o` |
| Shared dedicated `openclaw-reachy` identity | `SHA256:DQ3KpSgkP6Uev4zum1L4ObbOwNOyc/1cc8fb1oqHwK4` |

The verified ED25519 host-key fingerprints are:

| Host | SHA256 fingerprint |
| --- | --- |
| Crosstown MBP | `SHA256:p/+EyYg9X2mmHWvnpQiS6+Rhj0Uq5XbNhzEB9jrrru8` |
| Mac mini | `SHA256:tQUKmFRHQWVOShAsFJ15pn/6+X6uhhWiCZvgkGT7plk` |
| Reachy Mini | `SHA256:E6YjxmKDL1gKCrFvi0CVV7n4Jx8ACF8zdx8KD7Oo4/I` |

Verify a key from its public half with `ssh-keygen -lf`; never print a private
key:

```bash
ssh dylans-mac-mini 'ssh-keygen -lf ~/.ssh/id_mini_to_mbp.pub; ssh-keygen -lf ~/.ssh/openclaw-reachy.pub'
ssh dbochman@100.107.209.85 'ssh-keygen -lf ~/.ssh/id_rsa.pub; ssh-keygen -lf ~/.ssh/openclaw-reachy.pub'
```

## Tracked SSH policy

[`ssh_config`](../ssh_config) is symlinked to `~/.ssh/config` and follows
first-match-wins behavior:

- `~/.ssh/config.local` is included first for untracked machine-specific
  overrides.
- `dylans-mac-mini` identifies the OpenClaw host.
- `dylans-macbook-pro` identifies the Crosstown MBP.
- On the Mac mini, a `Match originalhost dylans-macbook-pro` block selects
  `~/.ssh/id_mini_to_mbp` and disables the 1Password agent.
- On a workstation with `~/.ssh/id_crosstown`, the matching block selects that
  dedicated local key. Without it, the default 1Password SSH agent is used.
- The default `Host *` block retains the 1Password SSH agent for ordinary
  attended access.

Persistent services do not rely on GUI agents or agent forwarding. The Reachy
relay LaunchAgents specify exact identities, `BatchMode=yes`,
`IdentityAgent=none`, `ExitOnForwardFailure=yes`, strict host-key checking, and
keepalives. The MBP upstream deliberately uses the numeric Mac mini Tailscale
address and `~/.ssh/id_rsa`; the Reachy relay deliberately uses Reachy's LAN IP
and `~/.ssh/openclaw-reachy`.

See [`openclaw/REACHY.md`](../openclaw/REACHY.md) for the ports, LaunchAgents,
control path, health checks, and gateway rollback procedure.

## Machine-local files

| Host | File | Required state |
| --- | --- | --- |
| Crosstown MBP | `~/.ssh/authorized_keys` | Owner-only; includes the workstation and Mac mini public identities |
| Crosstown MBP | `~/.ssh/id_rsa` | Mode `0600`; authenticates the MBP to the Mac mini upstream |
| Crosstown MBP | `~/.ssh/openclaw-reachy` | Mode `0600`; authenticates the MBP to Reachy |
| Crosstown MBP | `~/.ssh/known_hosts` | Contains verified numeric Mac mini and Reachy host keys |
| Mac mini | `~/.ssh/id_mini_to_mbp` | Mode `0600`; authenticates automation to the MBP |
| Mac mini | `~/.ssh/openclaw-reachy` | Mode `0600`; retained for rollback |
| Mac mini | `~/.ssh/authorized_keys` | Owner-only; includes the MBP `id_rsa.pub` identity |
| Reachy | `~/.ssh/authorized_keys` | Owner-only; includes the `openclaw-reachy` public identity |

Private keys are deliberately on disk for these unattended paths because the
1Password agent can require GUI approval or refuse signing in launchd/headless
contexts. Scope them to the intended hosts and do not copy them to unrelated
machines.

## Verification

Verify the workstation's direct MBP access:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=8 dbochman@100.107.209.85 'hostname; id -un'
```

Verify both host-to-host directions:

```bash
ssh dylans-mac-mini 'ssh -o BatchMode=yes -o IdentityAgent=none dylans-macbook-pro "hostname; id -un"'
ssh dbochman@100.107.209.85 'ssh -i ~/.ssh/id_rsa -o BatchMode=yes -o IdentityAgent=none dbochman@100.104.114.1 "hostname; id -un"'
```

Verify MBP-to-Reachy without changing robot state:

```bash
ssh dbochman@100.107.209.85 '~/.openclaw/bin/reachyctl status'
```

Check the selected identity policy before debugging authentication:

```bash
ssh -G dylans-macbook-pro | awk '$1 == "hostname" || $1 == "user" || $1 == "identityfile" || $1 == "identityagent"'
ssh dylans-mac-mini 'ssh -G dylans-macbook-pro | awk '\''$1 == "hostname" || $1 == "user" || $1 == "identityfile" || $1 == "identityagent"'\'''
```

All commands should complete noninteractively. An authentication prompt,
1Password popup, unknown-host prompt, or fallback to an unrelated key is a
configuration failure for an unattended path.

## Recovery and rotation

For a host-key warning, stop and compare the presented fingerprint through a
second trusted path or the host console. Do not blindly remove `known_hosts`
entries or use `StrictHostKeyChecking=no`.

For `Permission denied (publickey,...)`:

1. Confirm the source selects the intended identity with `ssh -G`.
2. Compare the local `.pub` fingerprint with the expected table.
3. From an already trusted path or the destination console, confirm the public
   identity remains in the correct account's `authorized_keys`.
4. Confirm `~/.ssh` is mode `0700`, private keys and `authorized_keys` are mode
   `0600`, and ownership matches the login account.
5. Keep `IdentityAgent=none` for dedicated unattended paths. Add
   `IdentitiesOnly=yes` when an alias or host configuration could otherwise add
   unrelated on-disk identities.

When rotating a dedicated key, add and verify the new public key before removing
the old one. Update every consuming host and LaunchAgent, confirm the fingerprint
through a trusted path, run the read-only checks above, and only then revoke the
old public key. Never rotate both sides of the MBP relay simultaneously without
retaining the Mac mini jump path.
