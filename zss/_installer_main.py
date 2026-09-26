"""Imperative shell for install.sh / uninstall.sh: backs up ~/.zshrc,
shows a diff of only the changed lines, asks for confirmation, and writes
the result. All the actual text transformation is in zss.installer
(unit-tested); this module is deliberately thin and untested I/O plumbing.

Never prints or logs the file's content, only the unified diff of what
would change (the same rule the install/uninstall scripts document, since
~/.zshrc commonly holds API tokens).
"""

import os
import sys
import time
from pathlib import Path

from zss import installer


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 2 or argv[0] not in ("install", "uninstall"):
        print("usage: _installer_main.py {install|uninstall} REPO_ROOT", file=sys.stderr)
        return 2
    mode, repo_root = argv[0], argv[1]

    zshrc = Path.home() / ".zshrc"
    if not zshrc.exists():
        print(f"error: {zshrc} does not exist", file=sys.stderr)
        return 1

    old_text = zshrc.read_text(encoding="utf-8")
    if mode == "install":
        new_text = installer.compute_install(old_text, repo_root)
    else:
        new_text = installer.compute_uninstall(old_text)

    if new_text == old_text:
        print(f"~/.zshrc already {'has' if mode == 'install' else 'has no'} zsh-smart-suggest block; nothing to do")
        return 0

    diff_text = installer.diff(old_text, new_text, filename="~/.zshrc")
    print(diff_text)

    try:
        resp = input(f"Apply the above change to ~/.zshrc? [y/N] ")
    except EOFError:
        resp = ""
    if resp.strip().lower() != "y":
        print("aborted; ~/.zshrc unchanged", file=sys.stderr)
        return 1

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = zshrc.with_name(f".zshrc.zss-backup-{timestamp}")
    backup_path.write_text(old_text, encoding="utf-8")
    os.chmod(backup_path, 0o600)

    zshrc.write_text(new_text, encoding="utf-8")
    os.chmod(zshrc, 0o600)

    print(f"done. Backup saved to {backup_path}")
    print("Restart your shell (or run `exec zsh`) to pick up the change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
