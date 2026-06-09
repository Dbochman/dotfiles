# Devcontainer SSH

The local SSH config provides two aliases for the `lpu-knowledge-base`
devcontainer:

```bash
# Open a plain shell in the container
ssh devc

# Attach directly to the lpu-kb tmux session
ssh lpu-kb-container
```

The `kb` zsh helper opens `lpu-kb-container` in a cmux workspace.

## Connection path

The laptop reaches the container through the work Mac and the dev VM:

```text
laptop -> work-mac (Tailscale) -> mydevvm (GCP IAP) -> 127.0.0.1:22390 -> container sshd
```

The VM publishes container port 22 only on VM loopback port `22390`. Do not
bind container SSH to `0.0.0.0`. The rootless-Docker container bridge address
is not directly reachable from the VM host, so the loopback-published port is
the supported route.

The container already authorizes the laptop's `~/.ssh/id_github` key. The
`devc` and `lpu-kb-container` host blocks select that key explicitly and bypass
the 1Password agent.

## Latency checks

Compare a plain container shell with the existing tmux and Codex session:

```bash
ssh devc
ssh lpu-kb-container
```

If `devc` is responsive but the existing Codex process is sticky, create a
fresh tmux window and compare a fresh Codex process. If both aliases are laggy,
the limiting path is likely the laptop-to-work-Mac-to-GCP-IAP connection.

SSH connection multiplexing reduces connection startup time, but it does not
reduce steady-state keystroke RTT.
