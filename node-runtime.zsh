# Shared PATH selection for interactive, login and noninteractive zsh shells.
# Source AFTER brew shellenv/path_helper/NVM so they cannot restore an old Node.
# Does not download runtimes or replace binaries in already-running processes.
if [[ -x "$HOME/.local/share/openclaw/runtimes/node-v24.22.1-darwin-arm64/bin/node" ]]; then
  path=("$HOME/.local/share/openclaw/runtimes/node-v24.22.1-darwin-arm64/bin" ${path:#$HOME/.local/share/openclaw/runtimes/node-v24.22.1-darwin-arm64/bin})
elif [[ -x "$HOME/.local/share/openclaw/runtimes/node-v26.12.1-darwin-arm64/bin/node" ]]; then
  path=("$HOME/.local/share/openclaw/runtimes/node-v26.12.1-darwin-arm64/bin" ${path:#$HOME/.local/share/openclaw/runtimes/node-v26.12.1-darwin-arm64/bin})
fi
export PATH
