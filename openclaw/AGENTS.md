# OpenClaw Automation Notes

Scope: this directory and its descendants unless a deeper `AGENTS.md` or `agents.md` overrides it.

- Treat the Financial Dashboard (`8585`) and Forecast Dashboard (`8586`) as separate services. `8585` publishes reconciled safe aggregates through `/api/forecast-baseline`; `8586` owns forecast state and the aggregate historical ledger.
- Preserve the daily dependency order: the cache-only finance refresh starts at 06:15 local time and runs Plaid before crypto, then Forecast ledger capture runs at 07:35. Do not move or add a capture job that can run before its source refresh without an explicit recovery design.
- The daily source-sync and ledger-capture paths must not invoke `op`, read Plaid secrets, or log raw account, transaction, token, or source-document data. Logs and status files may contain only operational metadata and aggregate identifiers.
- Deploy these Financial Dashboard and Forecast Dashboard LaunchAgents only to the Mac Mini. Do not bootstrap them on a local laptop merely because this checkout is present there.
- Keep wrapper scripts idempotent, lock concurrent work where needed, write protected runtime state atomically, and treat a transient service failure as retryable rather than a reason to erase cached data.
- When changing a Financial Dashboard, Forecast Dashboard, wrapper, or LaunchAgent contract, synchronize `FINANCIAL-DASHBOARD.md`, `FORECAST-DASHBOARD.md`, `DASHBOARDS.md`, `LAUNCHAGENTS.md`, and the relevant `bin/` or `logs/` README where applicable.
- After editing a Python wrapper, run `python3 -m py_compile`; after editing a LaunchAgent, run `plutil -lint`. Validate the affected loopback API and LaunchAgent state on the Mini before declaring deployment complete.

Follow the repository-level guidance in the root `AGENTS.md` first. The nested `workspace/AGENTS.md` governs the running OpenClaw workspace separately.
