"""Pure text transforms for editing ~/.zshrc: install.sh and uninstall.sh
both delegate the actual surgery here so it can be unit tested without
touching a real file. Never reads or writes ~/.zshrc itself — callers own
all I/O, backups, and confirmation prompts.
"""

from typing import List, Tuple

BLOCK_START = "# >>> zsh-smart-suggest >>>"
BLOCK_END = "# <<< zsh-smart-suggest <<<"
DISABLED_MARKER = "# zss:disabled"
OLD_PLUGIN_MARKERS = ("zsh-autosuggestions", "fast-syntax-highlighting", "zsh-syntax-highlighting")


def _is_source_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("source ") or stripped.startswith(". ") or "source " in stripped


def _find_block(lines: List[str]) -> Tuple[int, int]:
    """Return (start, end) inclusive indices of an existing zss block, or (-1, -1)."""
    start = end = -1
    for i, line in enumerate(lines):
        if line.strip() == BLOCK_START:
            start = i
        elif line.strip() == BLOCK_END and start != -1:
            end = i
            break
    if start != -1 and end != -1:
        return start, end
    return -1, -1


def _strip_existing_block(lines: List[str]) -> List[str]:
    start, end = _find_block(lines)
    if start == -1:
        return lines
    return lines[:start] + lines[end + 1:]


def _restore_disabled_lines(lines: List[str]) -> List[str]:
    """Undo a previous install's `# zss:disabled` comment-out, so re-running
    install (or running uninstall) always starts from the same baseline."""
    out = []
    for line in lines:
        if line.rstrip("\n").endswith(DISABLED_MARKER):
            restored = line.rstrip("\n")[: -len(DISABLED_MARKER)].rstrip()
            if restored.startswith("# "):
                restored = restored[2:]
            elif restored.startswith("#"):
                restored = restored[1:]
            out.append(restored + "\n" if line.endswith("\n") else restored)
        else:
            out.append(line)
    return out


def _matches_old_plugin(line: str) -> bool:
    if DISABLED_MARKER in line:
        return False
    if not _is_source_line(line):
        return False
    return any(marker in line for marker in OLD_PLUGIN_MARKERS)


def _disable_old_plugin_lines(lines: List[str]) -> Tuple[List[str], int]:
    """Comment out lines sourcing the old plugins. Returns (new_lines,
    index of the last such line), or index -1 if none were found."""
    out = []
    last_idx = -1
    for line in lines:
        if _matches_old_plugin(line):
            newline = "\n" if line.endswith("\n") else ""
            body = line.rstrip("\n")
            out.append(f"# {body}  {DISABLED_MARKER}{newline}")
            last_idx = len(out) - 1
        else:
            out.append(line)
    return out, last_idx


def _render_block(repo_root: str) -> List[str]:
    return [
        f"{BLOCK_START}\n",
        "HISTSIZE=50000\n",
        "SAVEHIST=50000\n",
        "setopt INC_APPEND_HISTORY HIST_FCNTL_LOCK\n",
        f'export PATH="{repo_root}/bin:$PATH"\n',
        f'source "{repo_root}/zsh-smart-suggest.plugin.zsh"\n',
        f"{BLOCK_END}\n",
    ]


def compute_install(text: str, repo_root: str) -> str:
    lines = text.splitlines(keepends=True)
    lines = _strip_existing_block(lines)
    lines = _restore_disabled_lines(lines)
    lines, last_idx = _disable_old_plugin_lines(lines)
    block = _render_block(repo_root)

    if last_idx == -1:
        # No known old plugin lines found: append at the end rather than
        # guess where the p10k instant-prompt block ends.
        new_lines = lines + (["\n"] if lines and not lines[-1].endswith("\n") else []) + block
    else:
        new_lines = lines[: last_idx + 1] + block + lines[last_idx + 1:]

    return "".join(new_lines)


def compute_uninstall(text: str) -> str:
    lines = text.splitlines(keepends=True)
    lines = _strip_existing_block(lines)
    lines = _restore_disabled_lines(lines)
    return "".join(lines)


def diff(old_text: str, new_text: str, filename: str = ".zshrc") -> str:
    import difflib

    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    return "".join(difflib.unified_diff(old_lines, new_lines, fromfile=filename, tofile=filename))
