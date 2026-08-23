#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)
TEST_ROOT=$(mktemp -d /tmp/hue-automations-test.XXXXXX)
cleanup() { rm -rf "$TEST_ROOT"; }
trap cleanup EXIT

export HOME="$TEST_ROOT/home"
export PATH="$TEST_ROOT/bin:/usr/bin:/bin:/opt/homebrew/bin"
export HUE_MODE=remote
mkdir -p "$HOME/.cache/hue/crosstown" "$TEST_ROOT/bin"
chmod 755 "$HOME/.cache/hue" "$HOME/.cache/hue/crosstown"
for key in remote_access_token remote_username; do
  printf '%s\n' "test-$key" > "$HOME/.cache/hue/crosstown/$key"
  chmod 644 "$HOME/.cache/hue/crosstown/$key"
done
printf '%s\n' unused-secret > "$HOME/.cache/hue/crosstown/unused_credential"
chmod 644 "$HOME/.cache/hue/crosstown/unused_credential"
printf '%s\n' true > "$TEST_ROOT/state"
: > "$TEST_ROOT/calls"
export FAKE_HUE_STATE="$TEST_ROOT/state"
export FAKE_HUE_CALLS="$TEST_ROOT/calls"

cat > "$TEST_ROOT/bin/curl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
method=GET
payload=
url=
while (($#)); do
  case "$1" in
    -X) method="$2"; shift 2 ;;
    -d) payload="$2"; shift 2 ;;
    -H|--connect-timeout|--max-time) shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
printf '%s\t%s\n' "$method" "$url" >> "$FAKE_HUE_CALLS"
enabled=$(tr -d '\n' < "$FAKE_HUE_STATE")
id=11111111-1111-1111-1111-111111111111
if [[ "$method" == PUT ]]; then
  [[ "$payload" == '{"enabled":false}' || "$payload" == '{"enabled":true}' ]]
  [[ "$payload" == *true* ]] && printf '%s\n' true > "$FAKE_HUE_STATE" || printf '%s\n' false > "$FAKE_HUE_STATE"
  printf '%s\n' '{"errors":[],"data":[{"rid":"11111111-1111-1111-1111-111111111111","rtype":"behavior_instance"}]}'
elif [[ "$url" == */behavior_instance/$id ]]; then
  printf '{"errors":[],"data":[{"id":"%s","enabled":%s,"metadata":{"name":"Potato Nightlight"}}]}\n' "$id" "$enabled"
else
  printf '{"errors":[],"data":[{"id":"%s","enabled":%s,"status":"running","metadata":{"name":"Potato Nightlight"},"configuration":{"when_extended":{"recurrence_days":["sunday","monday","tuesday","wednesday","thursday","friday","saturday"],"start_at":{"time_point":{"type":"time","time":{"hour":22,"minute":0}}}}}}]}\n' "$id" "$enabled"
fi
SH
chmod 755 "$TEST_ROOT/bin/curl"

inventory=$("$REPO_ROOT/bin/hue" --crosstown automations --json)
python3 -c 'import json,sys; v=json.loads(sys.argv[1]); assert v["automations"]==[{"enabled":True,"name":"Potato Nightlight","schedule":{"recurrence":"daily","when":"22:00"},"status":"running"}]; assert "id" not in sys.argv[1]' "$inventory"

disabled=$("$REPO_ROOT/bin/hue" --crosstown automation disable 'Potato Nightlight')
python3 -c 'import json,sys; v=json.loads(sys.argv[1]); assert v["ok"] and v["changed"] and v["enabled"] is False' "$disabled"
test "$(tr -d '\n' < "$TEST_ROOT/state")" = false

unchanged=$("$REPO_ROOT/bin/hue" --crosstown automation disable 'Potato Nightlight')
python3 -c 'import json,sys; v=json.loads(sys.argv[1]); assert v["ok"] and not v["changed"] and v["enabled"] is False' "$unchanged"
if "$REPO_ROOT/bin/hue" --crosstown automation enable Potato >/dev/null 2>&1; then
  echo "near-match automation name was accepted" >&2
  exit 1
fi

enabled=$("$REPO_ROOT/bin/hue" --crosstown automation enable 'Potato Nightlight')
python3 -c 'import json,sys; v=json.loads(sys.argv[1]); assert v["ok"] and v["changed"] and v["enabled"] is True' "$enabled"
test "$(stat -f %Lp "$HOME/.cache/hue")" = 700
test "$(stat -f %Lp "$HOME/.cache/hue/crosstown")" = 700
test "$(stat -f %Lp "$HOME/.cache/hue/crosstown/remote_access_token")" = 600
test "$(stat -f %Lp "$HOME/.cache/hue/crosstown/remote_username")" = 600
test "$(stat -f %Lp "$HOME/.cache/hue/crosstown/unused_credential")" = 600

printf '%s\n' 'test-hue-automations: PASS'
