# Remote Claude/Codex Inference via SSH

Setup for running Claude Code and Codex on a remote machine's enterprise subscription (e.g. the work MBP) from any local machine, over Tailscale.

## Why

The work MBP has an NVIDIA enterprise Claude subscription. Rather than copying auth tokens to personal machines (which may violate enterprise ToS and risks device-binding revocation), we SSH to the work MBP so all inference genuinely originates there.

## Functions

From `zshrc`:

```bash
# Usage: rcc [dir]  — defaults to ~ on the work MBP
rcc() {
  local dir="${1:-~}"
  ssh -t dylans-work-mbp "cd ${dir} && zsh -l -c /Users/dbochman/.local/bin/claude"
}
rcx() {
  local dir="${1:-~}"
  ssh -t dylans-work-mbp "cd ${dir} && zsh -l -c /opt/homebrew/bin/codex"
}
```

- `rcc` → remote Claude Code (at `~`)
- `rcc ~/repos/some-project` → remote Claude Code in a specific dir
- `rcx` / `rcx ~/repos/some-project` → same for Codex

## Copying repos to the work MBP

Use `rsync` over the same SSH alias:

```bash
rsync -avz --progress ~/repos/<name>/ dylans-work-mbp:~/repos/<name>/
```

Exclude build artifacts when relevant:

```bash
rsync -avz --progress --exclude node_modules --exclude .next --exclude dist \
  ~/repos/<name>/ dylans-work-mbp:~/repos/<name>/
```

Or `git clone` remotely if the repo has a remote:

```bash
ssh dylans-work-mbp 'git clone <url> ~/repos/<name>'
```

## One-time setup for a new local machine

### 1. SSH key

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_work_mbp -N "" -C "$(hostname)-to-work-mbp"
```

### 2. Authorize on the work MBP

Run on the **work MBP**:

```bash
mkdir -p ~/.ssh && \
  echo "<paste contents of ~/.ssh/id_work_mbp.pub>" >> ~/.ssh/authorized_keys && \
  chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys
```

### 3. SSH config entries

Already in `ssh_config` (this repo):

```
Host dylans-work-mbp
  HostName 100.73.15.5
  User dbochman

Match originalhost dylans-work-mbp exec "test -f ~/.ssh/id_work_mbp"
  IdentityFile ~/.ssh/id_work_mbp
  IdentityAgent none
```

The `Match` block bypasses 1Password agent for this key (it hangs under launchd / overflows auth attempts).

## One-time setup on the work MBP

### Keychain auto-lock

Remove the auto-lock timeout so the login keychain stays unlocked (required for any keychain reads over SSH — though we avoid keychain for Claude below):

```bash
security set-keychain-settings ~/Library/Keychains/login.keychain-db
```

### Claude Code — long-lived OAuth token

macOS keychain ACLs prevent SSH sessions from reading the normal Claude Code credential. Workaround: generate a long-lived token and export it via `.zshenv`.

On the **work MBP**:

```bash
claude setup-token
```

Paste the subscription OAuth token when prompted. It prints a token prefixed with `sk-ant-oat01-…`. Then:

```bash
echo 'export CLAUDE_CODE_OAUTH_TOKEN="sk-ant-oat01-..."' > ~/.zshenv
```

**Why `.zshenv` and not `.zshrc`:** zsh reads `.zshenv` for every invocation including non-interactive SSH commands. `.zshrc` is only read for interactive shells.

### Codex — ChatGPT login

Codex stores its auth in `~/.codex/auth.json` (not the keychain), so it works over SSH without extra setup:

```bash
codex login
```

### Keep awake

```bash
sudo pmset -a disablesleep 1 displaysleep 0
```

## Auth architecture

| Tool   | Auth storage on work MBP       | Works over SSH? | Notes                                                |
| ------ | ------------------------------ | --------------- | ---------------------------------------------------- |
| Claude | `CLAUDE_CODE_OAUTH_TOKEN` env  | Yes             | Keychain has ACL, unreadable via SSH → use env var   |
| Codex  | `~/.codex/auth.json` (file)    | Yes             | Plain file, no keychain issues                       |

Token lifetime: the Claude `setup-token` OAuth token is valid for 1 year. Regenerate before expiry.

## Troubleshooting

**"Not logged in" in Claude Code over SSH** — the keychain credential is being preferred over the env var. Delete `~/.claude/.credentials.json` on the work MBP and ensure `CLAUDE_CODE_OAUTH_TOKEN` is in `~/.zshenv`.

**"Too many authentication failures"** — 1Password agent is offering too many keys. The `Match` block in `ssh_config` uses `IdentityAgent none` to bypass it.

**"User interaction is not allowed" from `security`** — keychain is locked. Run `security set-keychain-settings ~/Library/Keychains/login.keychain-db` on the work MBP to remove auto-lock.
