#!/usr/bin/env bash
# Reverses install.sh: removes the zsh-smart-suggest block from ~/.zshrc and
# uncomments the zsh-autosuggestions / fast-syntax-highlighting lines it had
# disabled. Backs up ~/.zshrc first and asks for confirmation before writing.
# Then, if the remote-install clone exists ($ZSS_INSTALL_DIR, default
# ~/.zsh-smart-suggest), offers to delete it too.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ZSS_INSTALL_DIR="${ZSS_INSTALL_DIR:-$HOME/.zsh-smart-suggest}"

offer_remove_install_dir() {
  local dir="$ZSS_INSTALL_DIR"
  [ -d "$dir" ] || return 0
  # Only ever delete something that is recognisably our clone, so a
  # mistaken ZSS_INSTALL_DIR (e.g. $HOME) can't wipe unrelated files.
  if [ ! -f "$dir/zsh-smart-suggest.plugin.zsh" ] || [ ! -d "$dir/.git" ]; then
    echo "not removing $dir: it doesn't look like a zsh-smart-suggest clone" >&2
    return 0
  fi
  local resp=""
  read -r -p "Also delete $dir? [y/N] " resp || resp=""
  if [ "$(printf '%s' "$resp" | tr '[:upper:]' '[:lower:]')" = "y" ]; then
    rm -rf -- "$dir"
    echo "removed $dir"
  else
    echo "kept $dir"
  fi
}

# Keep the clone if the ~/.zshrc change was declined or failed: the block
# still sources files from it.
PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" /usr/bin/env python3 \
  "$REPO_ROOT/zss/_installer_main.py" uninstall "$REPO_ROOT"
offer_remove_install_dir
