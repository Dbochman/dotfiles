alias python=/usr/bin/python3
export PATH="$HOME/.local/bin:$PATH"

# 1Password SSH Agent
export SSH_AUTH_SOCK=~/Library/Group\ Containers/2BUA8C4S2C.com.1password/t/agent.sock

# Codex quick review alias (lower reasoning effort for speed)
alias codex-quick='codex -c model_reasoning_effort="medium"'
alias cq='codex-quick review'

# Remote Claude Code on work MBP
alias rcc='ssh -t dylans-work-mbp "zsh -l -c /Users/dbochman/.local/bin/claude"'
alias rcx='ssh -t dylans-work-mbp "zsh -l -c /opt/homebrew/bin/codex"'

# Chrome with remote debugging for MCP
alias chrome-debug='/Applications/Google\ Chrome\ Canary.app/Contents/MacOS/Google\ Chrome\ Canary --remote-debugging-port=9222 --user-data-dir="$HOME/.chrome-debug-profile"'

# npm global packages
export PATH="$HOME/.npm-global/bin:$PATH"

# API keys (local secrets file, chmod 600)
[[ -f "$HOME/.secrets" ]] && source "$HOME/.secrets"
