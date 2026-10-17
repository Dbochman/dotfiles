#!/usr/bin/env bash
# Operator/install-time packaging only; never invoked by a sensor status read.
set -euo pipefail
umask 077

openclaw_home="${1:-${OPENCLAW_HOME:-$HOME/.openclaw}}"
source_app="${AIRTHINGS_SOURCE_PYTHON_APP:-/opt/homebrew/opt/python@3.14/Frameworks/Python.framework/Versions/3.14/Resources/Python.app}"
source_binary="$source_app/Contents/MacOS/Python"
skill_dir="$(cd "$(dirname "$0")/.." && pwd)"
plist="$skill_dir/macos/Info.plist"
apps_dir="$openclaw_home/apps"
reader_app="$apps_dir/Airthings Reader.app"
stamp="$apps_dir/.airthings-reader-inputs"

if [[ ! -x "$source_binary" || ! -f "$plist" || -L "$plist" ]]; then
  printf 'Airthings Reader inputs are unavailable or unsafe.\n' >&2
  exit 1
fi
if [[ ! -d "$openclaw_home" || -L "$openclaw_home" \
    || -L "$apps_dir" || -L "$reader_app" || -L "$stamp" ]]; then
  printf 'Airthings Reader destination is unsafe.\n' >&2
  exit 1
fi
mkdir -p "$apps_dir"
[[ "$(/usr/bin/stat -f '%u' "$apps_dir")" = "$(id -u)" ]]
chmod 700 "$apps_dir"
inputs=$(/usr/bin/shasum -a 256 "$source_binary" "$plist" | /usr/bin/awk '{print $1}')

# Preserve the signed bundle and its macOS permission identity on no-op installs.
if [[ -f "$stamp" && -d "$reader_app" ]] \
    && [[ "$(<"$stamp")" = "$inputs" ]] \
    && cmp -s "$plist" "$reader_app/Contents/Info.plist" \
    && /usr/bin/codesign --verify --strict "$reader_app" 2>/dev/null; then
  printf 'Airthings Reader is unchanged.\n'
  exit 0
fi

staging=$(mktemp -d "$apps_dir/.airthings-reader-build.XXXXXX")
candidate="$staging/Airthings Reader.app"
mkdir -p "$candidate/Contents/MacOS"
cp "$plist" "$candidate/Contents/Info.plist"
cp "$source_binary" "$candidate/Contents/MacOS/Airthings Reader"
/usr/bin/plutil -lint "$candidate/Contents/Info.plist" >/dev/null
/usr/bin/codesign --force --sign - --identifier ai.openclaw.airthings-reader "$candidate"
/usr/bin/codesign --verify --strict "$candidate"

# Keep any prior bundle recoverable; never modify the Homebrew-owned app.
if [[ -e "$reader_app" ]]; then
  [[ -d "$reader_app" ]]
  mv "$reader_app" "$staging/previous.app"
fi
if ! mv "$candidate" "$reader_app"; then
  [[ ! -d "$staging/previous.app" ]] || mv "$staging/previous.app" "$reader_app"
  exit 1
fi
printf '%s\n' "$inputs" >"$staging/inputs"
mv "$staging/inputs" "$stamp"
if [[ -d "$staging/previous.app" ]]; then
  printf 'Previous reader retained at: %s/previous.app\n' "$staging"
else
  rmdir "$staging"
fi
printf 'Installed Airthings Reader. Allow its Bluetooth prompt on first use.\n'
