"""Curses history manager.

The selection/filter/sort logic lives in `Model`, a plain class with no
curses dependency, so it can be unit tested; `main()` is the thin curses
event loop wired on top of it.
"""

import curses
import locale
from dataclasses import dataclass, field
from typing import List, Optional

from zss import store


@dataclass
class Model:
    commands: List[store.Command] = field(default_factory=list)
    query: str = ""
    filtering: bool = False
    sort_mode: str = "recent"  # "recent" | "count"
    marked: set = field(default_factory=set)
    view: str = "history"  # "history" | "blocked" | "backups"
    cursor: int = 0

    def filtered(self) -> List[store.Command]:
        cmds = self.commands
        if self.query:
            cmds = [c for c in cmds if self.query in c.text]
        if self.sort_mode == "count":
            cmds = sorted(cmds, key=lambda c: (-c.count, -c.last_index))
        else:
            cmds = sorted(cmds, key=lambda c: -c.last_index)
        return cmds

    def clamp_cursor(self, count: int) -> None:
        if count == 0:
            self.cursor = 0
        else:
            self.cursor = max(0, min(self.cursor, count - 1))

    def move_cursor(self, delta: int, count: int) -> None:
        if count == 0:
            self.cursor = 0
            return
        self.cursor = max(0, min(self.cursor + delta, count - 1))

    def toggle_mark(self, text: str) -> None:
        if text in self.marked:
            self.marked.discard(text)
        else:
            self.marked.add(text)

    def toggle_sort(self) -> None:
        self.sort_mode = "count" if self.sort_mode == "recent" else "recent"

    def clear_marks(self) -> None:
        self.marked.clear()

    def set_query(self, query: str) -> None:
        self.query = query
        self.cursor = 0

    def start_filter(self) -> None:
        self.filtering = True
        self.query = ""
        self.cursor = 0

    def stop_filter(self, clear: bool = False) -> None:
        self.filtering = False
        if clear:
            self.query = ""
            self.cursor = 0


STATUS_HINT = (
    "/ filter | ↑/↓ move | space mark, d delete, k block, s sort, t blocked, b backups, q quit"
)
FILTER_STATUS_HINT = "type to filter | Enter apply | Esc cancel | backspace clear"


def key_action(query: str, filtering: bool, ch: int) -> str:
    """Classify a keypress against the current filter mode.

    `s`/`t`/`b`/`d`/`k`/`q`/space double as single-letter hotkeys and as
    ordinary characters someone would type into a filter (e.g. "docker"
    starts with 'd'). Deriving "am I filtering" from whether the query is
    empty made the very first character of such a query ambiguous — typing
    'd' to start filtering for "docker" fired the delete hotkey instead.
    Filtering is therefore an explicit mode, entered with '/' like less/vim:
    hotkeys only fire outside it, and every printable character (including a
    hotkey letter) is appended to the query while it is active.
    """
    if ch == curses.KEY_RESIZE:
        return "resize"
    if ch == curses.KEY_UP:
        return "up"
    if ch == curses.KEY_DOWN:
        return "down"
    if ch in (curses.KEY_BACKSPACE, 127, 8):
        return "backspace"

    if filtering:
        if ch == 27:
            return "cancel_filter"
        if ch in (10, 13, curses.KEY_ENTER):
            return "stop_filter"
        if ch == ord(" "):
            return "char: "
        if 32 < ch < 127:
            return f"char:{chr(ch)}"
        return ""

    if ch == 27:
        return "quit"
    if ch == ord("/"):
        return "start_filter"
    if ch == ord("q"):
        return "quit"
    if ch == ord("s"):
        return "sort"
    if ch == ord("t"):
        return "blocked"
    if ch == ord("b"):
        return "backups"
    if ch == ord("d"):
        return "delete"
    if ch == ord("k"):
        return "block"
    if ch == ord(" "):
        return "mark"
    return ""


def _addnstr(stdscr, y, x, text, w) -> None:
    try:
        stdscr.addnstr(y, x, text, max(w, 0))
    except curses.error:
        pass


def _draw_history(stdscr, model: Model, status: str) -> None:
    height, width = stdscr.getmaxyx()
    stdscr.erase()
    _addnstr(stdscr, 0, 0, f"/{model.query}", width - 1)

    rows = model.filtered()
    model.clamp_cursor(len(rows))
    list_height = max(height - 3, 0)
    top = max(0, model.cursor - list_height + 1)
    for i, cmd in enumerate(rows[top: top + list_height]):
        idx = top + i
        mark = "*" if cmd.text in model.marked else " "
        cursor_marker = ">" if idx == model.cursor else " "
        first_line = cmd.text.split("\n", 1)[0]
        suffix = " ⏎" if "\n" in cmd.text else ""
        line = f"{cursor_marker}{mark} {cmd.count:>4}  {first_line}{suffix}"
        _addnstr(stdscr, 1 + i, 0, line, width - 1)

    _addnstr(stdscr, height - 2, 0, "-" * (width - 1), width - 1)
    default_hint = FILTER_STATUS_HINT if model.filtering else STATUS_HINT
    _addnstr(stdscr, height - 1, 0, status or default_hint, width - 1)
    stdscr.refresh()


def _draw_list_view(stdscr, title: str, items: List[str], cursor: int, status: str) -> None:
    height, width = stdscr.getmaxyx()
    stdscr.erase()
    _addnstr(stdscr, 0, 0, title, width - 1)
    list_height = max(height - 3, 0)
    top = max(0, cursor - list_height + 1)
    for i, item in enumerate(items[top: top + list_height]):
        idx = top + i
        marker = ">" if idx == cursor else " "
        _addnstr(stdscr, 1 + i, 0, f"{marker} {item}", width - 1)
    _addnstr(stdscr, height - 2, 0, "-" * (width - 1), width - 1)
    _addnstr(stdscr, height - 1, 0, status, width - 1)
    stdscr.refresh()


def _confirm(stdscr, message: str) -> bool:
    height, width = stdscr.getmaxyx()
    _addnstr(stdscr, height - 1, 0, (message + " [y/N]").ljust(width - 1), width - 1)
    stdscr.refresh()
    ch = stdscr.getch()
    return ch in (ord("y"), ord("Y"))


def _edit_line(stdscr, prompt: str, initial: str) -> Optional[str]:
    """Minimal single-line editor prefilled with `initial`, so a "Block"
    action can start from the exact command text and let the user trim it
    (e.g. down to "printf ") before confirming. Enter accepts, Esc cancels."""
    height, width = stdscr.getmaxyx()
    buf = initial
    while True:
        _addnstr(stdscr, height - 1, 0, (prompt + buf).ljust(width - 1), width - 1)
        stdscr.refresh()
        ch = stdscr.getch()
        if ch == 27:
            return None
        if ch in (10, 13, curses.KEY_ENTER):
            return buf
        if ch in (curses.KEY_BACKSPACE, 127, 8):
            buf = buf[:-1]
        elif 32 <= ch < 127:
            buf += chr(ch)


BLOCKED_STATUS_HINT = "e edit rule | a allow again | q back"


def _edit_blocked_rule(stdscr, old: str, p) -> str:
    """Edit block rule `old` in place (it keeps its position in the list).
    Prefilled with the current prefix; shows how many history entries the new
    prefix will delete before asking to confirm. Returns a status line."""
    new = _edit_line(stdscr, "edit rule: ", old)
    if not new or new == old:
        return "unchanged"
    try:
        store.validate_block_rule(new)
    except ValueError as e:
        return str(e)
    if new in store.block_list(p):
        return "that prefix is already blocked"
    count = store.count_matching([new], match="prefix", p=p)
    extra = f" {count} matching history entr{'y' if count == 1 else 'ies'} will be deleted." if count else ""
    if not _confirm(stdscr, f'Change "{old}" to "{new}"?{extra}'):
        return "cancelled"
    try:
        result = store.block_edit(old, new, p)
    except store.LockTimeout as e:
        return str(e)
    if not result.found:
        return "that rule is no longer in the block list"
    return f"rule updated; removed {result.delete_result.removed} entries"


def _run_blocked(stdscr, p) -> None:
    cursor = 0
    status = BLOCKED_STATUS_HINT
    while True:
        items = store.block_list(p)
        if cursor >= len(items):
            cursor = max(0, len(items) - 1)
        _draw_list_view(stdscr, "Blocked prefixes", items, cursor, status)
        status = BLOCKED_STATUS_HINT
        ch = stdscr.getch()
        if ch in (ord("q"), 27):
            return
        if ch == curses.KEY_UP:
            cursor = max(0, cursor - 1)
        elif ch == curses.KEY_DOWN:
            cursor = min(max(0, len(items) - 1), cursor + 1)
        elif ch == ord("a") and items:
            store.block_remove(items[cursor], p)
            status = "allowed again"
        elif ch == ord("e") and items:
            status = _edit_blocked_rule(stdscr, items[cursor], p)


def _run_backups(stdscr, p) -> None:
    cursor = 0
    status = "r restore | q back"
    while True:
        items = store.list_backups(p)
        if cursor >= len(items):
            cursor = max(0, len(items) - 1)
        _draw_list_view(stdscr, "Backups", items, cursor, status)
        status = "r restore | q back"
        ch = stdscr.getch()
        if ch in (ord("q"), 27):
            return
        if ch == curses.KEY_UP:
            cursor = max(0, cursor - 1)
        elif ch == curses.KEY_DOWN:
            cursor = min(max(0, len(items) - 1), cursor + 1)
        elif ch == ord("r") and items:
            if _confirm(stdscr, f"Restore {items[cursor]}? Current history is backed up first."):
                store.restore(items[cursor], p)
                status = "restored"


def _run(stdscr) -> int:
    curses.curs_set(0)
    p = store.paths()
    model = Model(commands=store.unique_commands(store.load(p)))
    status = ""

    while True:
        _draw_history(stdscr, model, status)
        status = ""
        ch = stdscr.getch()
        action = key_action(model.query, model.filtering, ch)

        if action == "quit":
            return 0
        if action == "resize":
            continue
        if action == "sort":
            model.toggle_sort()
        elif action == "up":
            model.move_cursor(-1, len(model.filtered()))
        elif action == "down":
            model.move_cursor(1, len(model.filtered()))
        elif action == "mark":
            rows = model.filtered()
            if rows:
                model.toggle_mark(rows[model.cursor].text)
        elif action == "blocked":
            _run_blocked(stdscr, p)
        elif action == "backups":
            _run_backups(stdscr, p)
        elif action == "delete":
            rows = model.filtered()
            targets = list(model.marked) or ([rows[model.cursor].text] if rows else [])
            if not targets:
                status = "nothing to delete"
                continue
            if _confirm(stdscr, f"Delete {len(targets)} command(s)? A backup is made first."):
                result = store.delete(targets, match="exact", p=p)
                status = f"removed {result.removed} entries; backup {result.backup_id}"
                model.clear_marks()
                model.commands = store.unique_commands(store.load(p))
        elif action == "block":
            rows = model.filtered()
            targets = list(model.marked) or ([rows[model.cursor].text] if rows else [])
            if not targets:
                status = "nothing to block"
                continue
            if len(targets) == 1:
                edited = _edit_line(stdscr, "block prefix: ", targets[0])
                if edited:
                    count = store.count_matching([edited], match="prefix", p=p)
                    extra = f" {count} matching history entr{'y' if count == 1 else 'ies'} will be deleted." if count else ""
                    try:
                        store.validate_block_rule(edited)
                    except ValueError as e:
                        status = str(e)
                        continue
                    if _confirm(stdscr, f'Block "{edited}"?{extra}'):
                        result = store.block_add(edited, p)
                        status = f"blocked; removed {result.delete_result.removed} entries"
                        model.clear_marks()
                        model.commands = store.unique_commands(store.load(p))
            else:
                try:
                    for t in targets:
                        store.validate_block_rule(t)
                except ValueError as e:
                    status = f"{e}: {t!r}"
                    continue
                count = store.count_matching(targets, match="prefix", p=p)
                extra = f" {count} matching history entr{'y' if count == 1 else 'ies'} will be deleted." if count else ""
                if _confirm(stdscr, f"Block {len(targets)} command(s) exactly as-is?{extra}"):
                    removed = 0
                    for t in targets:
                        removed += store.block_add(t, p).delete_result.removed
                    status = f"blocked {len(targets)} rule(s); removed {removed} entries"
                    model.clear_marks()
                    model.commands = store.unique_commands(store.load(p))
        elif action == "start_filter":
            model.start_filter()
        elif action == "stop_filter":
            model.stop_filter()
        elif action == "cancel_filter":
            model.stop_filter(clear=True)
        elif action == "backspace":
            model.set_query(model.query[:-1])
        elif action.startswith("char:"):
            model.set_query(model.query + action[len("char:"):])

    return 0


def main() -> int:
    locale.setlocale(locale.LC_ALL, "")
    return curses.wrapper(_run)
