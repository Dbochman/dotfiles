#!/bin/bash
set -euo pipefail
set -a
source /Users/dbochman/.openclaw/rest980/env-j5
set +a
cd /Users/dbochman/.openclaw/rest980-app
if [ -x /opt/homebrew/opt/node@22/bin/node ]; then
  NODE=/opt/homebrew/opt/node@22/bin/node
elif [ -x /opt/homebrew/bin/node ]; then
  NODE=/opt/homebrew/bin/node
else
  NODE="$(command -v node)"
fi
exec "$NODE" bin/www
