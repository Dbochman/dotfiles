# Managed desktop job workflow

## Choose a scope

Use the default `linux` scope for Python, shell, media, SQLite, archive, and
other WSL-native jobs. Its managed root is on the Linux filesystem for good
small-file and package performance.

Use `windows` only when the job needs a PowerShell script, a Windows-native
application, or a Windows path. Both scopes can see the RTX 5090 through WSL;
scope describes where the managed job lives and which script types are
accepted, not whether GPU compute is available.

The internal `splat` scope preserves the established remote-splat workspace.
Use the `remote-splat` skill rather than selecting it directly.

## Job layout

Every job has four fixed directories:

```text
<job>/
  input/     staged source files and the approved script
  outputs/   final artifacts eligible for verified retrieval
  logs/      bounded operational output read by progress
  state/     run hash and exit status
```

Scripts start with the job directory as their working directory. Refer to
`input/` and `outputs/` relatively so the same script remains inspectable and
portable. Do not write credentials into the job tree or logs.

## Durable execution

Linux sessions are named `compute-linux-<job>` and Windows sessions are named
`compute-windows-<job>`. The session remains after the command exits so
`progress` can distinguish running, succeeded, and failed states. The helper
rejects a second run while that session exists; use a new job name for a new
attempt rather than overwriting the evidence from the first run.

The `plan-run` response is the approval boundary. It reports the exact staged
script hash and size. The dispatcher independently recomputes the hash at run
time and refuses a changed script.

## Transfer behavior

`stage` walks regular files only and keeps their relative paths. Empty
directories are not materialized because they carry no job input. An already
present file with the same size and SHA-256 is reused. No stage operation
deletes remote files.

`fetch` first binds to remote metadata, streams the artifact through the
restricted dispatcher, and verifies the local temporary file before an atomic
rename. A matching local file is reused; a different file with the same name
causes a fail-closed result.
