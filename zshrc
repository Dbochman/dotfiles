alias python=/usr/bin/python3
export PATH="$HOME/.local/bin:$PATH"

# Prefer Homebrew's keg-only Node 22 for CLIs with `env node` shebangs.
export PATH="/opt/homebrew/opt/node@22/bin:$PATH"

# 1Password SSH Agent
export SSH_AUTH_SOCK=~/Library/Group\ Containers/2BUA8C4S2C.com.1password/t/agent.sock

# Codex quick review alias (lower reasoning effort for speed)
alias codex-quick='codex -c model_reasoning_effort="medium"'
alias cq='codex-quick review'

# Open the devcontainer SSH target in cmux.
devc() {
  if ! cmux ping >/dev/null 2>&1; then
    open /Applications/cmux.app >/dev/null 2>&1

    local i
    for i in {1..100}; do
      cmux ping >/dev/null 2>&1 && break
      sleep 0.1
    done
  fi

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
