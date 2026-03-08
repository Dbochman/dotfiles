#!/bin/bash
CMD=$(cat /tmp/bt_command.txt 2>/dev/null)
~/.openclaw/bin/bt_connect $CMD > /tmp/bt_result.txt 2>&1
echo $? > /tmp/bt_exit.txt
