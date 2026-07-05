#!/usr/bin/env bash

# Shared, side-effect-bounded helpers for copying OpenClaw skill trees.

openclaw_skill_path_is_deployment_artifact() {
  local relative_path="${1#./}"
  local basename="${relative_path##*/}"

  case "/$relative_path/" in
    */__pycache__/*|*/.pytest_cache/*|*/.mypy_cache/*|*/.ruff_cache/*)
      return 0
      ;;
  esac

  case "$basename" in
    *.pyc|*.pyo|.DS_Store|*.local.md)
      return 0
      ;;
  esac

  return 1
}

prune_openclaw_skill_copy() {
  local root="$1"
  [[ -d "$root" && ! -L "$root" ]] || return 1

  find "$root" -depth \
    \( -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache \) \
       -o -type f \( -name '*.pyc' -o -name '*.pyo' -o -name .DS_Store -o -name '*.local.md' \) \) \
    -exec rm -rf -- {} +
}

copy_openclaw_skill_tree() {
  local src="${1%/}"
  local dst="$2"
  [[ -d "$src" && ! -L "$src" && ! -e "$dst" && ! -L "$dst" ]] || return 1

  cp -R "$src" "$dst" || return 1
  find "$dst" -type l -delete || return 1
  prune_openclaw_skill_copy "$dst"
}
