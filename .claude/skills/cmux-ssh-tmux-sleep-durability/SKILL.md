---
name: cmux-ssh-tmux-sleep-durability
description: >-
  Diagnose and fix cmux SSH workspaces that show stale terminals or repeated
  "Remote daemon bootstrap failed: failed to query remote platform: Connection
  to HOST closed" errors after macOS sleep or network changes while the remote
  tmux/Codex session remains alive. Use when hardening a cmux alias that runs
  remote tmux, choosing SSH keepalives, or adding automatic SSH reattachment
  without turning clean tmux detach into a reconnect loop.
---

# Harden cmux SSH + tmux Across Sleep

Preserve the process in remote tmux and make the local SSH client disposable. Diagnose cmux's managed connection before choosing its native reconnect path or a generic cmux workspace with an explicit reconnect wrapper.

## Diagnose the Layers

1. Inspect cmux and the visible terminal:

   ```bash
   cmux version
   cmux tree --all
   cmux top --all --processes --flat
   cmux ssh-session-list --all-workspaces
   cmux read-screen --workspace <ref> --scrollback --lines 200
   ```

2. Confirm the remote tmux session survived independently:

   ```bash
   ssh <host> 'tmux list-sessions -F "#{session_name}:#{session_attached}:#{session_windows}"'
   ```

3. Inspect effective SSH settings. A large keepalive product delays detection; for example, `ServerAliveInterval 30` with `ServerAliveCountMax 60` can take roughly 30 minutes to abandon an unresponsive connection.

   ```bash
   ssh -G <host> | rg '^(hostname|user|requesttty|remotecommand|controlmaster|controlpath|serveralive) '
   ```

4. Compare a batch platform probe with and without a forced TTY:

   ```bash
   ssh -o BatchMode=yes -o ConnectTimeout=8 <host> \
     'printf "home=%s os=%s arch=%s\n" "$HOME" "$(uname -s)" "$(uname -m)"'
   ssh -o BatchMode=yes -o ConnectTimeout=8 -o RequestTTY=force <host> \
     'printf "home=%s os=%s arch=%s\n" "$HOME" "$(uname -s)" "$(uname -m)"'
   ```

Treat a cmux error tag and a live remote tmux session as separate facts: the local managed workspace may be broken while Codex continues remotely.

## Recognize the cmux TTY Conflict

Watch for an alias shaped like:

```bash
cmux ssh <host> --ssh-option RequestTTY=force -- tmux new-session -A -s <name> || \
  cmux new-workspace --command 'ssh -t ...'
```

In affected cmux builds, `RequestTTY=force` is stored on the remote workspace and can leak into noninteractive daemon/platform probes. Removing it makes the probe sane, but a tmux remote command then has no TTY and exits. The shell fallback is also ineffective for later failures because `cmux ssh` returns success after creating the workspace; daemon bootstrap happens asynchronously.

Verify this against the installed cmux version rather than assuming every release behaves identically. Prefer native `cmux ssh` when its foreground TTY and background probe options are separated correctly.

## Use a Reconnecting Local Wrapper

When native managed SSH is conflicted, create a normal cmux workspace whose initial command runs a wrapper like this:

```bash
while :; do
  ssh -tt \
    -o ConnectTimeout=8 \
    -o ConnectionAttempts=1 \
    -o ServerAliveInterval=10 \
    -o ServerAliveCountMax=3 \
    -o ControlMaster=no \
    -o ControlPath=none \
    -- <host> tmux new-session -A -s <session>
  status=$?

  [[ $status -eq 255 ]] || exit "$status"
  sleep <bounded-backoff>
done
```

Apply these invariants:

- Run tmux on the remote host; it owns the Codex process across SSH loss.
- Force a TTY only on the foreground SSH client.
- Retry only OpenSSH status 255. Status 0 means a clean tmux detach; other statuses may be real tmux or command errors.
- Use a bounded backoff and allow Ctrl-C/HUP/TERM to stop the wrapper.
- Disable ControlMaster/ControlPath for this client so a stale multiplex socket cannot be reused.
- Reset backoff after a connection was stable.
- Quote or validate the host, tmux binary, and session name before forming a remote command.

Create the workspace with the wrapper as its command. This sacrifices managed-remote features such as cmux's remote browser proxy and drag-and-drop relay, so keep the workaround scoped to the affected alias.

## Add cmux Relaunch Metadata

From inside the new surface, register the exact wrapper as a custom resume command:

```bash
cmux surface resume set \
  --kind tmux \
  --checkpoint "ssh-<host>-<session>" \
  --source cmux-ssh-tmux \
  --cwd "$HOME" \
  --shell '<wrapper> <host> <session> <tmux-binary>'
cmux surface resume show --json
```

In cmux 0.64.17, a binding whose source is the default `cli` is silently signed with `manual` policy, and **Settings > Terminal > Resume Commands** only opens `cmux.json`; it does not provide an approval control. Use a stable, non-`cli` integration source to make cmux show its one-time **Auto-Restore / Ask Each Time / Keep Manual** dialog. Review the exact executable and arguments before choosing Auto-Restore. Do not edit the resulting `policy` directly in JSON because the record is HMAC-signed and changing signed fields invalidates it.

Pass a stable `--cwd` such as `$HOME` when the wrapper does not depend on its launch directory. Approval matching includes the working directory, so inheriting arbitrary project directories would create repeated prompts and separate records.

## Verify

1. Run `bash -n` on the wrapper, `zsh -n` on the alias file, and `git diff --check`.
2. Attach a disposable remote tmux session through a temporary cmux workspace.
3. Confirm `tmux list-sessions` reports an attached client.
4. Use a fake SSH executable that returns 255 once and 0 next; verify two identical attach attempts and a successful wrapper exit.
5. Detach or kill only the disposable tmux session, close the temporary workspace, and confirm no test session remains.
6. Verify a clean tmux detach exits rather than reconnecting.

Do not test by killing the user's live tmux session. A laptop-sleep fix is successful when the SSH client can die and reattach while the remote session and Codex process keep their identity.

## References

- [cmux SSH documentation](https://cmux.com/docs/ssh)
- [cmux session restore and custom resume commands](https://cmux.com/docs/session-restore)
- [OpenSSH client keepalive settings](https://man.openbsd.org/ssh_config#ServerAliveCountMax)
- [OpenSSH exit status](https://man.openbsd.org/ssh.1#EXIT_STATUS)
- [tmux sessions and `new-session -A`](https://man.openbsd.org/tmux.1)
