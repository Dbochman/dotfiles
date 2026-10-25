# Ola retirement

Ola was fully retired on October 25, 2026. The owner confirmed revocation and
termination through `olachat.com`. The provider action was owner-confirmed;
local verification did not submit another authenticated request with the old
API key or send a message.

## Removed integration

- The public `/hooks/wake` Funnel route was removed on October 24, followed by
  removal of the member-wide Funnel permission on October 25.
- The Ola MCP server and the heartbeat's inbox/read/reply routine were removed
  from tracked and deployed OpenClaw configuration.
- The local hook configuration is explicitly disabled. Its former token and
  `hook:ola` default session configuration were removed.
- `OLA_API_KEY`, `OLA_HOOK_TOKEN`, and `OLA_WEBHOOK_SECRET` were removed from the
  required refresh set, the deployed general cache, and their `OP_REF_` seed
  references. Unrelated cache and seed assignments were preserved.
- The bridge scripts, LaunchAgent plist, and special deployment/reload handling
  were removed from source and from installed executable/LaunchAgent locations.
  The launchd disable override remains as an additional guard.
- The gateway was restarted to unload its old MCP connection and process
  environment. Historical chat data and operational logs were not deleted.

The refresh helper no longer reads or exports retired Ola values even when an
older seed/cache still contains them. Removing a local value is not provider
revocation; the owner completed that separate step through Ola's portal.

## Verification

The deployed gateway restarted with a new PID and returned a healthy response.
An unauthenticated POST to its former `/hooks/wake` path returned HTTP 404;
port 18790 refused connections. The live config has no Ola MCP entry, hooks
are disabled, and the deployed heartbeat has no Ola routine. The cache and
seed contain no Ola assignments. Private Tailscale Serve remains separate;
see [Tailscale security operations](../docs/tailscale-security.md).

Regression checks:

```bash
python3 -m unittest openclaw.tests.test_ola_integration \
  openclaw.tests.test_secret_cache
bash -n openclaw/bin/openclaw-refresh-secrets \
  openclaw/bin/dotfiles-pull.command
```

The retirement and secret-refresh behavior passed. The broader secret-cache
suite also checks repository-wide private identifiers; its pre-existing
failure identifies two unchanged presence scripts. That unrelated finding
must not be mistaken for a retirement or refresh failure.

## Historical material and restoration

The [July setup feedback](OLA-FEEDBACK.md) is historical. The retired bridge
implementation and its tests remain available in Git history; it is no longer
part of routine deployment. A protected local retirement record is under
`~/.local/state/ola-retirement/20261025/` on the Mini. Historical configuration
backups must not be copied over the live config during unrelated recovery.

Reintroduction would be a new owner-approved integration: enroll a new provider
credential, review tool permissions and any inbox instructions, and validate
configuration before deployment. A public callback would additionally require
a reviewed device-scoped Funnel grant. Do not recover a revoked API key or
restore member-wide Funnel permission as routine maintenance.
