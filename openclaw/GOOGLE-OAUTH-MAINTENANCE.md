# Google OAuth maintenance completion record

**Completed November 3, 2026, on the Mac Mini.** The inactive-client notice,
November 11 AI-agent classification notice, and resulting Nest credential split
are resolved for the integrations reviewed. Production changes are deployed and
pushed. The remaining Living Room camera issue is a device-availability follow-up.

## Notices and outcomes

| Work | Completed action | Validation |
| --- | --- | --- |
| Inactive clients in `homeautomation-486703` | Matched the installed Workspace and Nest clients to their Console entries; retained both required clients | Both existing clients showed recent activity; Workspace and Nest API reads succeeded |
| Workspace agent classification | Enabled and verified the saved AI-agent flag on **Desktop client 1**, used by the installed Workspace CLI | Gmail, Calendar, and Drive read operations succeeded after the change |
| Nest mixed use | Kept **HomeAutomation** for conventional services; created **OpenClaw Nest Agent** with its own agent-marked OAuth client and Device Access project | Both grants independently listed four thermostats and three cameras; new-token renewal and production canaries passed |

The inactivity notice concerned individual OAuth clients, not deletion of the
whole Cloud project. Google provides advance notice before deleting inactive
clients. Its agent designation identifies an agent workflow without granting
additional scopes; mixed workflows use separate client IDs. See
[Google's OAuth client management guidance](https://support.google.com/cloud/answer/15549257?hl=en).

The November 11 classification work did not require replacing the working GWS
CLI or installing a new Workspace MCP server. No additional Google change is
pending from these reviewed notices.

## Production ownership

| Client | Consumers | Current routing |
| --- | --- | --- |
| Desktop client 1 | Workspace agent operations through `gws` | Existing Workspace credentials; AI-agent flag enabled |
| HomeAutomation | Conventional Nest climate collection, event ingestion, and scheduled vacancy automation | Existing client, grant, token cache, camera/event policy, and Pub/Sub subscription retained |
| OpenClaw Nest Agent | Agent thermostat commands, household routine skills, requested camera media, and model-analysis camera callers | Dedicated credentials and tokens under `~/.openclaw/nest-agent/`; explicit `nest-agent` command; protected camera switch enabled |

The new Device Access project has Pub/Sub disabled. The existing self-hosted
topic remains the sole event source. Agent failures stop the request instead of
falling back to conventional credentials. Google consent branding can still say
HomeAutomation because the clients share the Cloud project's brand; compare the
actual client identities privately when diagnosing account routing.

## Validation and remaining device issue

- The local callback validated the single-use OAuth state and completed the
  token exchange without putting codes or secrets in command arguments.
- Agent refresh-token renewal and both site Eco dry runs passed. A same-state
  Solarium OFF command passed with readback; thermostat modes, Eco states, and
  setpoints remained unchanged.
- Kitchen and Living Room Wired returned valid private JPEGs. The production
  camera helper also passed capture and cleanup after routing switched. No media
  was sent or retained.
- The gateway reports `nest-thermostat`, `nest-camera`, `cabin-routines`, and
  `crosstown-routines` eligible and model-visible. A persisted disabled camera
  skill entry was enabled after its helper passed validation.
- The original event listener and climate dashboard kept their running PIDs.
  Worker checks, conventional inventory, climate collection, and HTTP health
  checks remained healthy. Offline test results are in the
  [Nest operations record](NEST-OAUTH.md#implementation-verification).

**Remaining issue:** the non-wired Living Room camera returned HTTP 400 with
both the old and new clients. Dylan suspects low battery or an underspecified
USB power supply; the physical cause has not been confirmed. Its verified agent
binding remains active. Restore adequate power and, on an authorized camera
request, verify capture and cleanup again. This does not require another OAuth
migration unless new authentication evidence appears.

## Recovery and future maintenance

The **OpenClaw Nest Agent** item in 1Password holds the new client ID, client
secret, project ID, redirect URI, and refresh token. Exact field references and
runtime credentials are protected local files. Unattended services never call
1Password. The final refresh-token backup succeeded using an attended terminal
and a protected template file after earlier CLI timeouts.

Use [Nest OAuth operations](NEST-OAUTH.md) for enrollment, secure exchange,
credential paths, cutover, and rollback. The registered agent redirect is
`http://127.0.0.1:18797/oauth/callback`; the migration's listener was temporary
and is no longer running. A future exchange needs an attended listener or the
full callback URL supplied through private stdin as documented there.

For another inactivity notice, identify the exact affected client in Console,
match it privately to its consumer, and check recent activity before acting.
For an agent authentication failure, repair that context rather than replacing
the conventional Device Access binding. Rollback must restore routing and
affected skill files together while retaining both grants and event/history
state; protected before-files and a manifest remain on the Mini.

## Delivered changes

The rollout was pushed to `origin/main` through `4bc76d0`:

- [a1f63a3](https://github.com/Dbochman/dotfiles/commit/a1f63a3): secure authorization exchange and separate agent credentials.
- [3430383](https://github.com/Dbochman/dotfiles/commit/3430383): production agent skill routing.
- [4bc76d0](https://github.com/Dbochman/dotfiles/commit/4bc76d0): standalone camera command publication and production validation record.

The [completed migration plan](plans/nest-agent-oauth-migration.md) retains the
design, pre-migration baseline, and validation gates. Client secrets, tokens,
authorization callbacks, and exact device-resource IDs are excluded from Git.
