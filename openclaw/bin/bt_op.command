#!/bin/bash
# bt_op.command — GUI-session Bluetooth launcher for OpenClaw
# Must run via `open /tmp/bt_op.command` so IOBluetooth has proper API access
# Reads command from /tmp/bt_command.txt, runs blueutil, writes exit code

CMD=$(cat /tmp/bt_command.txt 2>/dev/null)
if [ -z "$CMD" ]; then
  echo "No command in /tmp/bt_command.txt" > /tmp/bt_result.txt
  echo "1" > /tmp/bt_exit.txt
  osascript -e "tell application \"Terminal\" to close (every window whose name contains \"bt_op\")" &>/dev/null &
  exit 1
fi

ACTION=$(echo "$CMD" | awk "{print \$1}")
MAC=$(echo "$CMD" | awk "{print \$2}")

export PATH="/opt/homebrew/bin:$PATH"

case "$ACTION" in
  connect)
    blueutil --connect "$MAC" > /tmp/bt_result.txt 2>&1
    ;;
  disconnect)
    blueutil --disconnect "$MAC" > /tmp/bt_result.txt 2>&1
    ;;
  *)
    echo "Unknown action: $ACTION" > /tmp/bt_result.txt
    echo "1" > /tmp/bt_exit.txt
    osascript -e "tell application \"Terminal\" to close (every window whose name contains \"bt_op\")" &>/dev/null &
    exit 1
    ;;
esac

echo "$?" > /tmp/bt_exit.txt

# Close Terminal window after completion
osascript -e "tell application \"Terminal\" to close (every window whose name contains \"bt_op\")" &>/dev/null &
