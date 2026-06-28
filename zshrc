if [[ -x /opt/homebrew/bin/brew ]]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi

alias python=/usr/bin/python3
export PATH="$HOME/.local/bin:$PATH"

# Prefer Homebrew's keg-only Node 22 for CLIs with `env node` shebangs.
export PATH="/opt/homebrew/opt/node@22/bin:$PATH"

# 1Password SSH Agent
export SSH_AUTH_SOCK=~/Library/Group\ Containers/2BUA8C4S2C.com.1password/t/agent.sock

# Codex quick review alias (lower reasoning effort for speed)
alias codex-quick='codex -c model_reasoning_effort="medium"'
alias cq='codex-quick review'

_cmux_ensure_running() {
  if ! command -v cmux >/dev/null 2>&1; then
    echo "cmux not found. Install cmux.app or add its CLI to PATH." >&2
    return 127
  fi

  if cmux ping >/dev/null 2>&1; then
    return 0
  fi

  open /Applications/cmux.app >/dev/null 2>&1

  local i
  for i in {1..100}; do
    cmux ping >/dev/null 2>&1 && return 0
    sleep 0.1
  done

  echo "cmux did not become ready." >&2
  return 1
}

# Open the devcontainer SSH target in cmux.
devc() {
  _cmux_ensure_running || return

  cmux ssh devc --name devc "$@" || \
    cmux new-workspace --name devc --command "ssh devc"
}

# Remote Claude Code / Codex on work MBP
# Usage: rcc [--dagenerously-skip-permissions] [dir]  — defaults to ~ on the work MBP
# Rewrites local home prefix so ~/repos/foo works naturally.
_rcc_remote_dir() {
  local dir="${1:-$HOME}"
  # Replace local home prefix with remote home
  echo "${dir/#$HOME//Users/dbochman}"
}
rcc() {
  local dir="$HOME"
  local claude_cmd="/Users/dbochman/.local/bin/claude"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dagenerously-skip-permissions)
        claude_cmd="${claude_cmd} --dagenerously-skip-permissions"
        shift
        ;;
      *)
        dir="$1"
        shift
        ;;
    esac
  done

  dir=$(_rcc_remote_dir "$dir")
  ssh -t dylans-work-mbp "cd '${dir}' && zsh -l -c '${claude_cmd}'"
}
# Usage: rcx [--flag ...] [dir]  — any --* flags forwarded to codex
rcx() {
  local dir="$HOME"
  local -a codex_args

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --*)
        codex_args+=("$1")
        shift
        ;;
      *)
        dir="$1"
        shift
        ;;
    esac
  done

  dir=$(_rcc_remote_dir "$dir")
  ssh -t dylans-work-mbp "cd '${dir}' && zsh -l -c '/opt/homebrew/bin/codex ${codex_args[*]}'"
}

# Attach the lpu-kb tmux session using the dedicated SSH alias.
kb() {
  _cmux_ensure_running || return

  cmux ssh lpu-kb-container --name lpu-kb "$@" || \
    cmux new-workspace --name lpu-kb --command "ssh lpu-kb-container"
}

# Attach (or create) the 'work' tmux session on the Work MBP, inside a cmux workspace.
opwork() {
  _cmux_ensure_running || return

  cmux ssh work-mac --name "op-research work" --ssh-option RequestTTY=force "$@" -- \
    tmux new-session -A -s work || \
    cmux new-workspace --name "op-research work" \
      --command "ssh -t work-mac tmux new-session -A -s work"
}

# Attach (or create) a remote tmux session in a reconnecting cmux workspace.
_cmux_ssh_tmux() {
  local workspace_name="$1"
  local host="$2"
  local session_name="$3"
  local tmux_bin="$4"
  shift 4

  _cmux_ensure_running || return

  local helper="$HOME/.local/bin/cmux-ssh-tmux"
  if [[ ! -x "$helper" ]]; then
    echo "cmux-ssh-tmux not found. Run ./install.sh from the dotfiles repo." >&2
    return 127
  fi

  local command="${(q)helper} ${(q)host} ${(q)session_name} ${(q)tmux_bin}"
  local -a cmux_args
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --no-focus)
        cmux_args+=(--focus false)
        ;;
      *)
        cmux_args+=("$1")
        ;;
    esac
    shift
  done

  cmux workspace create --name "$workspace_name" --command "$command" "${cmux_args[@]}"
}

# Attach (or create) the 'home' tmux session on the Mac Mini. The local SSH
# client reconnects after sleep/network changes; tmux keeps Codex alive remotely.
home() {
  _cmux_ssh_tmux home dylans-mac-mini home /opt/homebrew/bin/tmux "$@"
}

# Chrome with remote debugging for MCP
alias chrome-debug='/Applications/Google\ Chrome\ Canary.app/Contents/MacOS/Google\ Chrome\ Canary --remote-debugging-port=9222 --user-data-dir="$HOME/.chrome-debug-profile"'

# npm global packages
export PATH="$HOME/.npm-global/bin:$PATH"

# API keys (local secrets file, chmod 600)
[[ -f "$HOME/.secrets" ]] && source "$HOME/.secrets"

unset ANTHROPIC_API_KEY
unset ANTHROPIC_BASE_URL
unset ANTHROPIC_MODEL
unset ANTHROPIC_SMALL_FAST_MODEL

# Machine-local shell customizations. Keep host-specific paths out of dotfiles.
[[ -f "$HOME/.zshrc.local" ]] && source "$HOME/.zshrc.local"
