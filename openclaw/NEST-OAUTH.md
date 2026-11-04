# Nest OAuth contexts

The separate agent context is staged for migration; deployment and cloud
enrollment are still pending. The migration gates and rollback are in
[the migration plan](plans/nest-agent-oauth-migration.md).

## Ownership and routing

| Caller | Credentials and state |
| --- | --- |
| Conventional `nest`, climate snapshot/dashboard, event ingestion | Existing HomeAutomation context, combined protected cache, `~/.cache/nest-sdm/`, existing camera/event policy |
| Explicit `nest-agent` thermostat/read commands | `~/.openclaw/nest-agent/credentials/client.json`, `tokens/oauth-tokens.json` |
| Agent still/video helper, activity reviewer, entry verifier, home-event camera worker | Conventional before cutover; `nest-agent` after the protected routing switch is enabled |

Agent commands ignore inherited credential, token-cache, project, and camera
policy overrides. Their client **and** project must differ from the conventional
identity. Their camera policy is fixed at `~/.openclaw/nest-agent/config/cameras.json`
and must contain verified resources from the new project. Conventional event
ingestion keeps the old policy and subscription. Agent commands do not offer
history writes, dashboard management, or display-name camera capture.

Directories are owner-owned mode 0700; JSON files are mode 0600, regular files,
without symlinks or hard links. Credential/token writes use atomic replacement.
Token bundles are bound to the client/project pair; mismatches and refresh
failures stop the request. The internal `nest-oauth.py token` action emits a
bearer token only into the CLI's private pipe: do not invoke it for diagnostics.

## Attended agent enrollment

Create the separate cloud client and Device Access project using the migration
plan. Save their credentials in the dedicated 1Password item. Prepare the
owner-only directories and `credentials/references.json` with exact field
references, using this schema (replace placeholders privately):

```sh
umask 077
mkdir -p ~/.openclaw/nest-agent/{credentials,tokens,config}
chmod 700 ~/.openclaw/nest-agent ~/.openclaw/nest-agent/{credentials,tokens,config}
```

```json
{
  "client_id": "op://OpenClaw/OpenClaw Nest Agent/clientID",
  "client_secret": "op://OpenClaw/OpenClaw Nest Agent/client_secret",
  "project_id": "op://OpenClaw/OpenClaw Nest Agent/project_id",
  "redirect_uri": "https://www.google.com"
}
```

The redirect must be registered on the client. An optional `refresh_token`
exact-field reference can seed an already-authorized new context; omit it for
first authorization. Never put literal credentials in the references file.

In an attended terminal, run:

```sh
openclaw-refresh-secrets --nest-agent --interactive
```

Confirm `CONFIGURE` at the prompt. Only the specified fields are read; the
combined secrets cache is not rewritten. Unattended commands never invoke `op`.
Materialization leaves routing unchanged and refuses a different identity when
an existing agent token bundle belongs to another client/project.

## Secure authorization

Run `~/.openclaw/bin/nest-agent reauth` and open its generated consent URL.
Select all intended devices. Within ten minutes, pass the **full callback URL**
to the exchange via stdin. This attended example uses hidden terminal input;
the callback never appears in arguments or shell history:

```sh
/usr/bin/python3 -c 'import getpass,sys; sys.stdout.write(getpass.getpass("Full callback URL: "))' | ~/.openclaw/bin/nest-agent reauth --stdin
```

The exchange validates the exact redirect and single-use state, decodes the
code correctly, and sends a bounded HTTPS POST without proxy inheritance or
redirect following. Provider responses and credentials are not logged. Once an
exchange starts, its state is consumed even if the response fails; begin a new
flow to retry. Failure preserves the previous token bundle.

Call `nest-agent raw` privately to complete activation and verify inventory.
Do not log device identifiers. Live consent/state forwarding, concurrent access
under both clients, token renewal, and device controls still require the runbook's
live validation. Offline tests use fake credentials and transports only.

The conventional flow uses `nest reauth` and `nest reauth --stdin` with the same
protection. Its successful exchange creates `~/.cache/nest-sdm/oauth-tokens.json`,
which takes priority over legacy token files. Existing conventional grants keep
using their legacy cache until reauthorization; agent tokens never use it.

## Deploy and cut over

Deploy `nest`, `nest-agent`, `nest-oauth.py`, and `nest_routing.py` together before
the four camera callers. The installer and managed pull handle dependencies;
keep both tracked `nest` copies identical. Reload affected workers after a routing
module update. No credential file or routing switch is shipped by installation.

Only after both projects pass inventory, renewal, camera-binding, continuity,
and authorized live-canary gates, atomically write the protected
`~/.openclaw/nest-agent/routing.json`:

```json
{"version": 1, "agent_enabled": true}
```

The four camera callers read this switch for each request. Enable it only after
the new exact camera policy is ready and in-flight media requests have finished.
Then update agent thermostat/agent vacancy command instructions to `nest-agent`.
Conventional scheduled collectors and vacancy automation retain their existing
context. Enabling the switch never falls back after an agent failure. A malformed
or insecure routing file stops capture. Before cutover, an absent switch uses
the conventional route. An explicit `false` is the attended rollback route;
restore the affected agent instructions as well. Preserve both grants and all
event/history state throughout.

## Implementation verification

Offline verification on November 3, 2026:

- 171 Nest tests passed, including 22 authorization/routing tests. The new tests
  also passed under macOS `/usr/bin/python3` (3.9).
- 30 deployment-contract, 5 entry-verifier, and 16 home-event camera tests passed.
- Bash syntax, Python compilation, synchronized CLI copies, `git diff --check`,
  and `./sync.sh validate` passed.
- The broader secret-cache and home-event deployment suites each have one
  unrelated failure reproduced on unchanged `main`: Samsung skill private
  identifiers and the older Crosstown presence-launcher expectation.
- `./install.sh --dry-run` exited 2 in the isolated checkout: existing links
  point to the main checkout, and the Peekaboo nested-symlink guard rejected its
  existing link. Focused installer/deployer fixtures passed; no install was run.

No live OAuth grant, routing, device setting, or running service was changed.
Google requires an initial device-list call to complete a new grant; that live
gate remains pending. See [Nest account authorization](https://developers.google.com/nest/device-access/authorize).
