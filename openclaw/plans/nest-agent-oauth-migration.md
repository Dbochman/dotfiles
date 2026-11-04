# Nest agent OAuth migration

**Status:** Deployed and active on November 3, 2026. The new agent client/project,
protected credentials, token renewal, inventory matching, thermostat canary,
and two available camera canaries passed. All four migrated skills are visible
in the running gateway. The non-wired Living Room camera returns HTTP 400 with
both grants; its suspected power issue is an existing device exception, not a
migration regression. See [production validation and rollback](../NEST-OAUTH.md).

## Decision

Create a separate **OpenClaw Nest Agent** OAuth client, marked as agentic, and a
second Device Access project with the same display name. Keep the existing
**HomeAutomation** client and Device Access project for conventional collection
and event ingestion. Move agent callers to the new project only after its
credentials, device permissions, and exact camera bindings pass validation.

Google permits up to three Device Access projects per account, each with a
unique OAuth client. The account currently has one project. The registration
fee is per account, not per additional project; an unexpected payment or new
terms prompt requires review before proceeding. See Google's
[project creation requirements](https://developers.google.com/nest/device-access/project/create).

Do **not** replace the OAuth client on the existing Device Access project.
Google documents that this would invalidate existing access tokens. A second
project provides a path to validate the new integration alongside the old one;
successful concurrent authorization still needs to be demonstrated locally.
See [modifying a project](https://developers.google.com/nest/device-access/project/modify).

The November 11 Workspace MCP deadline does not require a Nest cutover.
The Workspace client has already been marked agentic. Google requires separate
clients for agent and conventional workflows; a new agentic client can still
be created after the existing-client classification becomes fixed. See
[OAuth client classification](https://support.google.com/cloud/answer/15549257?hl=en).

## Verified baseline

- Device Access Console lists one active Sandbox project, **HomeAutomation**.
- Its OAuth client matches the protected `NEST_CLIENT_ID` used by the Mini.
- A read-only SDM inventory request succeeded: four thermostats and three cameras.
- The protected camera policy contains exactly `Kitchen`, `Living Room`, and
  `Living Room Wired`, all under the existing Device Access project.
- The climate dashboard returned HTTP 200 with a current snapshot and 14 rooms
  across its climate providers. This is a collection/UI check, not a command test.
- `bin/nest`, `openclaw/bin/nest`, and the deployed `~/.openclaw/bin/nest` were
  byte-identical. `/opt/homebrew/bin/nest` points through `dotfiles/bin/nest`.
- The installed Nest snapshot, dashboard, listener, and reviewer LaunchAgents
  have no independent OAuth-client or token-cache overrides.
- The existing Pub/Sub topic is self-hosted in `homeautomation-486703`, not
  `sdm-prod`. The November 30 Google-hosted-topic retirement therefore does not
  require changing this event stream.

No camera media was captured and no thermostat settings were changed for this
baseline. Exact project/device identifiers and credentials stay in protected
local configuration rather than this document.

## Caller routing

| Caller | Target credential context | Required work |
| --- | --- | --- |
| `nest snapshot`, climate history, `nest-dashboard.py`, conventional dashboard reads | Existing HomeAutomation | Preserve current collection, history, and default CLI behavior |
| `nest-event-listener.py`, its Pub/Sub service account, and `nest-home-event-bridge.py` | Existing event project and subscription | Preserve event source, database, cursors, and normalized aliases |
| OpenClaw `nest-thermostat` skill and agent-run vacancy actions | New agent client/project | Route through an explicit `nest-agent` entry point |
| `nest-camera-image` still and video requests | New agent client/project | Use the new entry point and a separate exact camera policy |
| `nest-activity-reviewer.py` and `cabin-entry-verifier.py` camera requests | New agent client/project | Change the capture adapter while retaining event input and all existing presence/privacy gates |
| `home-event-camera.py` camera requests used for model analysis | New agent client/project | Change only the Nest capture adapter; preserve Ring routing and delivery rules |

The dashboard reads historical collection output, while model-driven consumers
can continue reading normalized events from the existing listener. They must
resolve an event's safe camera alias to the **new** capture policy. An old
project resource identifier must never be forwarded directly to the new API.

There must remain one event publisher/subscription path. Leave Pub/Sub disabled
on the new Device Access project initially to avoid duplicate household events.

## Implemented and deployed support

1. Add an explicit `nest-agent` wrapper and one validated agent context loader.
   Bind the OAuth client, Device Access project, token directory, and camera
   policy together. Missing or inconsistent agent configuration must fail closed,
   without falling back to conventional credentials or calling `op` unattended.
2. Use a dedicated 1Password item, **OpenClaw Nest Agent**, with exact-field
   references for client ID, client secret, Device Access project ID, and refresh
   token. Extend the attended secret-materialization workflow to write a separate
   owner-only context. Preserve the existing Google Nest item and combined cache.
3. Reserve `~/.openclaw/nest-agent/` for the new context: `credentials/` for
   credentials, `tokens/` for refresh/access tokens, and `config/cameras.json`
   for exact camera bindings. Directories must be owner-only, credential files
   mode 0600, with symlink rejection and atomic writes. These paths are now deployed in production.
4. Key cached tokens to their client/project identity. A fresh legacy access token
   must never satisfy an agent request. Clear inherited `NEST_*` credential and
   cache overrides before loading the selected context. Keep weather/location
   configuration separate from OAuth credential selection.
5. Replace the old argv-based code exchange with `reauth --stdin`. The full
   callback must match a ten-minute, single-use state and expected redirect.
   Credentials travel on a private descriptor and the HTTPS request body stays
   in memory. Timeouts are bounded; redirects, raw provider errors, and partial
   token responses are rejected. Tokens are replaced atomically after validation.
6. Update the fixed capture entry points and their sanitized child environments.
   Merely exporting `NEST_CACHE_DIR` in a shell is insufficient:
   `nest-camera-image` and the reviewer helpers deliberately rebuild their child
   environments. Retain their command allowlists and media cleanup behavior.
7. Keep `bin/nest` and `openclaw/bin/nest` synchronized if the shared CLI changes.
   Develop in an isolated checkout: editing `bin/nest` in the live dotfiles
   checkout changes the command reached through `/opt/homebrew/bin/nest`.
   Stage tested files before deploying any caller changes.

The agent context must not expose history-writing or service-management commands
as a shortcut to the conventional context. Define its allowed commands explicitly.
Agent support should initially cover status, exact thermostat controls, site Eco
preflight/control, and the protected camera capture operations used above.

## Cloud setup and authorization

1. Create the web OAuth client **OpenClaw Nest Agent** in the existing Cloud
   project, with **Use this client for an AI-powered agent** enabled. Register
   only the redirect URI implemented by the reviewed authorization helper.
   Store the one-time displayed secret directly in the dedicated protected
   credential workflow; do not print, screenshot, or commit it.
2. Create a second Device Access project, **OpenClaw Nest Agent**, linked to that
   new OAuth client. Confirm the old project's client and Pub/Sub settings remain
   unchanged. Do not add another event subscription or alter existing IAM roles.
3. Authorize the new project through Partner Connections Manager using the
   device-owning account. Request the existing `sdm.service` scope with offline
   access and explicit consent. Select the required Cabin and Crosstown devices.
   Keep the old project's grant in place.
4. Exchange the code into the isolated agent token directory, then call
   `devices.list` to complete authorization. Google documents this API call as
   part of activation. See
   [account authorization](https://developers.google.com/nest/device-access/authorize).
5. Discover the new project's resources. Match devices by verified site, type,
   and room/alias; require an unambiguous one-to-one match. Treat IDs as potentially
   project-specific. Do not copy old IDs or change only an enterprise prefix.
6. Build the new three-camera policy with the same alias/site/capture rules as
   the existing protected policy. Preserve the old policy for event ingestion.
   Keep the cross-project resource mapping private and out of logs.

A successful login alone is not cutover readiness. Both the new and existing
projects must pass the checks below after authorization.

## Validation and cutover gates

| Check | Pass condition |
| --- | --- |
| Identity isolation | Different client IDs and Device Access projects; separate credentials and token stores; no legacy fallback |
| Token renewal | A fresh access token can be obtained using the new refresh token without printing it or changing the old cache |
| Inventory | Both projects independently list the expected four thermostats and three cameras, matched by site and type |
| Camera binding | All three aliases resolve uniquely under the new project; wrong-project, duplicate, and missing bindings are rejected |
| Thermostat routing | Both `eco-site cabin on --dry-run` and `eco-site crosstown on --dry-run` validate the correct inventory without writes |
| Existing service continuity | The old client still authenticates; climate snapshots continue; event ingestion and consumer cursors remain healthy |
| Capture routing | Fake transport tests prove every agent capture caller uses the new project and existing cleanup/privacy gates |
| Controlled live commands | Separately authorized thermostat and camera canaries succeed with readback; do not capture media merely to prepare migration |
| Failure handling | Missing secrets, wrong permissions, refresh failure, and mismatched mappings fail without device changes or credential fallback |

After those gates pass, deploy the wrapper/context support, then migrate agent
callers in a bounded maintenance window. Change the skills only when their new
entry point is deployed and verified. Reload only services whose executable or
environment changed; preserve the existing snapshot collector and listener.
Check for in-flight camera work before switching its adapters.

Retain the old client, tokens, event policy, service account, topic, subscription,
and history throughout cutover. Do not revoke a grant, rotate a secret, edit the
old project binding, or delete a client as part of this migration.

## Rollback

Before deployment, record checksums and make owner-only backups of the affected
executables, skills, LaunchAgents, and protected configurations. Never put token
contents in an audit report or Git. Keep the backup and restore manifest scoped
to files that will actually change.

If validation fails before cutover, stop staging and leave existing callers in
place. After cutover, stop new agent requests, allow or safely clean up in-flight
media work, and restore the previous caller routing and affected service files.
This deliberately returns to the previous mixed-use state while the migration
is repaired; it is not an automatic runtime fallback. Restore only affected
files/services, then verify old-client inventory, collection, and event health.

Because the existing Device Access binding was never replaced, rollback should
not require reauthorizing it. Verify this during the concurrent-access checks;
do not assume that restoring configuration can recover a revoked Google grant.

## Implementation and test scope

- Shared CLI copies: `bin/nest`, `openclaw/bin/nest`; new explicit agent wrapper
  and context loader; `openclaw/bin/openclaw-refresh-secrets`.
- Capture callers: `nest-camera-image`, `nest-activity-reviewer.py`,
  `cabin-entry-verifier.py`, `home-event-camera.py`, and their relevant wrappers.
- Agent routing: `skills/nest-thermostat/SKILL.md`,
  `skills/nest-camera/SKILL.md`, and the agent vacancy instructions/call sites.
- Deployment: `install.sh` and `openclaw/bin/dotfiles-pull.command`; check both
  CLI source copies and the deployed `~/.openclaw/bin` entry points.
- Extend existing fake-only tests for secret-cache handling, Nest mutations,
  camera capture and cleanup, activity review, entry verification, and home-event
  camera routing. Add cross-context token reuse and missing-context rejection
  cases before any credential is enrolled.
- Run Python compilation, Bash syntax checks, relevant tests, and plist lint
  for any changed LaunchAgents. Update operational docs alongside the eventual
  deployed contract, including explicit conventional versus agent ownership.

Production migration is complete with the documented unavailable-camera
exception. Preserve this runbook for future reenrollment and rollback; recheck
the non-wired Living Room camera once power is restored.
