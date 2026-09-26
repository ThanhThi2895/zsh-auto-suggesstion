#!/usr/bin/env bash
# Installs zsh-smart-suggest into ~/.zshrc: comments out zsh-autosuggestions
# and fast-syntax-highlighting (never `brew uninstall`s them) and inserts a
# marked block that sources this plugin instead. Backs up ~/.zshrc first,
# shows a diff of only the changed lines, and asks for confirmation before
# writing anything. Re-running is safe (idempotent).
#
# Two ways to run it:
#   ./install.sh                     from a checkout: installs that checkout.
#   curl -fsSL .../install.sh | bash remote: clones the repo into
#                                    $ZSS_INSTALL_DIR (or, if our clone is
#                                    already there, points it at
#                                    $ZSS_REPO_URL/$ZSS_BRANCH and fast-forwards
#                                    it), then installs from that clone.
#
# Remote-mode overrides: ZSS_INSTALL_DIR (default ~/.zsh-smart-suggest),
# ZSS_REPO_URL, ZSS_BRANCH (default main).
#
# Everything runs from main() on the last line, so a truncated download via
# `curl | bash` executes nothing.
set -euo pipefail

ZSS_REPO_URL="${ZSS_REPO_URL:-https://github.com/ThanhThi2895/zsh-auto-suggesstion.git}"
ZSS_BRANCH="${ZSS_BRANCH:-main}"
ZSS_INSTALL_DIR="${ZSS_INSTALL_DIR:-$HOME/.zsh-smart-suggest}"

install_from() {
  local repo_root="$1"
  PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" exec /usr/bin/env python3 \
    "$repo_root/zss/_installer_main.py" install "$repo_root"
}

fetch_repo() {
  local dir="$1"
  if ! command -v git >/dev/null 2>&1; then
    echo "error: git is required to install from GitHub" >&2
    exit 1
  fi
  if [ -e "$dir" ]; then
    # Only ever pull into something that is recognisably our clone (the
    # same check uninstall.sh makes before deleting it), so a mistaken
    # ZSS_INSTALL_DIR can't retarget an unrelated repo.
    if [ ! -d "$dir/.git" ] || [ ! -f "$dir/zsh-smart-suggest.plugin.zsh" ]; then
      echo "error: $dir exists but is not a zsh-smart-suggest git clone; move it away or set ZSS_INSTALL_DIR" >&2
      exit 1
    fi
    echo "Updating $dir from $ZSS_REPO_URL ($ZSS_BRANCH)"
    git -C "$dir" remote set-url origin "$ZSS_REPO_URL"
    git -C "$dir" fetch --quiet origin "+refs/heads/$ZSS_BRANCH:refs/remotes/origin/$ZSS_BRANCH"
    if git -C "$dir" show-ref --verify --quiet "refs/heads/$ZSS_BRANCH"; then
      git -C "$dir" checkout --quiet "$ZSS_BRANCH"
    else
      git -C "$dir" checkout --quiet -b "$ZSS_BRANCH" --track "origin/$ZSS_BRANCH"
    fi
    git -C "$dir" pull --ff-only --quiet origin "$ZSS_BRANCH"
  else
    echo "Cloning $ZSS_REPO_URL into $dir"
    git clone --quiet --branch "$ZSS_BRANCH" "$ZSS_REPO_URL" "$dir"
  fi
}

main() {
  local script_path="${BASH_SOURCE[0]:-}"
  if [ -n "$script_path" ] && [ -f "$(dirname "$script_path")/zss/_installer_main.py" ]; then
    install_from "$(cd "$(dirname "$script_path")" && pwd)"
  fi

  fetch_repo "$ZSS_INSTALL_DIR"
  # Under `curl | bash` stdin is the script itself, so the [y/N] prompt
  # would read EOF and abort; answer it from the terminal instead when
  # there is one.
  if [ ! -t 0 ] && { : </dev/tty; } 2>/dev/null; then
    exec </dev/tty
  fi
  install_from "$ZSS_INSTALL_DIR"
}

main "$@"
