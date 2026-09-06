#!/usr/bin/env bash
# Read-only runtime canary for the desktop's WSL and Windows-native toolchain.
set -euo pipefail

gpu_probe=/usr/lib/wsl/lib/nvidia-smi
tool_root=/mnt/c/Users/Owner/Documents/Codex/2026-09-05/c/work

test -x "$gpu_probe"
test -f "$tool_root/colmap/bin/colmap.exe"
test -f "$tool_root/brush/brush_app.exe"
test -f "$tool_root/conversion/node_modules/.bin/splat-transform.cmd"

gpu_name="$($gpu_probe --query-gpu=name --format=csv,noheader | head -n 1)"
case "$gpu_name" in
  *"RTX 5090"*) ;;
  *) exit 1 ;;
esac

printf 'remote-splat compute canary passed\n'
