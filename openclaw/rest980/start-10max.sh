#!/bin/bash
set -euo pipefail
set -a
source /Users/dbochman/.openclaw/rest980/env-10max
set +a
/usr/bin/python3 /Users/dbochman/.openclaw/rest980/ensure-openclaw-routes.py
cd /Users/dbochman/.openclaw/rest980-app
NODE=/Users/dbochman/.local/share/openclaw/runtimes/node-v24.22.1-darwin-arm64/bin/node
if [[ ! -x "$NODE" ]] || [[ "$(/usr/bin/shasum -a 256 "$NODE" | /usr/bin/awk '{print $1}')" != 6f32069480ec14d52b6d7b1f3c7124cfbbc46203abf5ddf5c65b127435f3f329 ]]; then
  echo "Verified patched Node runtime required" >&2
  exit 1
fi
export PATH="$(dirname "$NODE"):/opt/homebrew/bin:/usr/bin:/bin"
exec "$NODE" bin/www
