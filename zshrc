alias python=/usr/bin/python3
export PATH="$HOME/.local/bin:$PATH"

# 1Password SSH Agent
export SSH_AUTH_SOCK=~/Library/Group\ Containers/2BUA8C4S2C.com.1password/t/agent.sock

# Codex quick review alias (lower reasoning effort for speed)
alias codex-quick='codex -c model_reasoning_effort="medium"'
alias cq='codex-quick review'

# Remote Claude Code / Codex on work MBP
# Usage: rcc [dir]  — defaults to ~ on the work MBP
# Rewrites local home prefix so ~/repos/foo works naturally.
_rcc_remote_dir() {
  local dir="${1:-$HOME}"
  # Replace local home prefix with remote home
  echo "${dir/#$HOME//Users/dbochman}"
}
rcc() {
  local dir; dir=$(_rcc_remote_dir "$1")
  ssh -t dylans-work-mbp "cd '${dir}' && zsh -l -c /Users/dbochman/.local/bin/claude"
}
rcx() {
  local dir; dir=$(_rcc_remote_dir "$1")
  ssh -t dylans-work-mbp "cd '${dir}' && zsh -l -c /opt/homebrew/bin/codex"
}

# Chrome with remote debugging for MCP
alias chrome-debug='/Applications/Google\ Chrome\ Canary.app/Contents/MacOS/Google\ Chrome\ Canary --remote-debugging-port=9222 --user-data-dir="$HOME/.chrome-debug-profile"'

# npm global packages
export PATH="$HOME/.npm-global/bin:$PATH"

# API keys (local secrets file, chmod 600)
[[ -f "$HOME/.secrets" ]] && source "$HOME/.secrets"
