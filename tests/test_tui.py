import curses
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from zss import history, store
from zss.tui import Model, _edit_blocked_rule, key_action


def cmd(text, count=1, last_index=0, last_ts=None):
    return store.Command(text=text, count=count, last_index=last_index, last_ts=last_ts)


class TestModel(unittest.TestCase):
    def test_filter_by_query(self):
        m = Model(commands=[cmd("git status"), cmd("ls -la")])
        m.set_query("git")
        self.assertEqual([c.text for c in m.filtered()], ["git status"])

    def test_sort_recent_by_last_index(self):
        m = Model(commands=[cmd("a", last_index=0), cmd("b", last_index=2), cmd("c", last_index=1)])
        self.assertEqual([c.text for c in m.filtered()], ["b", "c", "a"])

    def test_sort_count(self):
        m = Model(commands=[cmd("a", count=1), cmd("b", count=5), cmd("c", count=3)])
        m.toggle_sort()
        self.assertEqual(m.sort_mode, "count")
        self.assertEqual([c.text for c in m.filtered()], ["b", "c", "a"])

    def test_toggle_mark(self):
        m = Model()
        m.toggle_mark("x")
        self.assertIn("x", m.marked)
        m.toggle_mark("x")
        self.assertNotIn("x", m.marked)

    def test_move_cursor_clamped(self):
        m = Model(commands=[cmd("a"), cmd("b")])
        m.move_cursor(-5, len(m.filtered()))
        self.assertEqual(m.cursor, 0)
        m.move_cursor(5, len(m.filtered()))
        self.assertEqual(m.cursor, 1)

    def test_query_resets_cursor(self):
        m = Model(commands=[cmd("a"), cmd("b")], cursor=1)
        m.set_query("a")
        self.assertEqual(m.cursor, 0)

    def test_start_filter_enters_filtering_with_empty_query(self):
        m = Model(query="old", cursor=1)
        m.start_filter()
        self.assertTrue(m.filtering)
        self.assertEqual(m.query, "")
        self.assertEqual(m.cursor, 0)

    def test_stop_filter_keeps_query_by_default(self):
        m = Model(filtering=True, query="git")
        m.stop_filter()
        self.assertFalse(m.filtering)
        self.assertEqual(m.query, "git")

    def test_stop_filter_can_clear_query(self):
        m = Model(filtering=True, query="git")
        m.stop_filter(clear=True)
        self.assertFalse(m.filtering)
        self.assertEqual(m.query, "")


class TestKeyAction(unittest.TestCase):
    def test_hotkeys_fire_outside_filtering(self):
        self.assertEqual(key_action("", False, ord("s")), "sort")
        self.assertEqual(key_action("", False, ord("t")), "blocked")
        self.assertEqual(key_action("", False, ord("b")), "backups")
        self.assertEqual(key_action("", False, ord("d")), "delete")
        self.assertEqual(key_action("", False, ord("k")), "block")
        self.assertEqual(key_action("", False, ord("q")), "quit")
        self.assertEqual(key_action("", False, ord(" ")), "mark")

    def test_slash_enters_filter_mode_outside_filtering(self):
        self.assertEqual(key_action("", False, ord("/")), "start_filter")

    def test_hotkey_letters_type_into_the_filter_when_filtering(self):
        # Regression: hotkey letters used to be inferred from "is the query
        # empty", so a filter that STARTS with a hotkey letter (e.g. typing
        # 'd' to search for "docker") fired the hotkey instead of typing.
        # Filtering is now an explicit mode entered with '/', so every
        # printable character types into the query regardless of position.
        self.assertEqual(key_action("", True, ord("d")), "char:d")
        self.assertEqual(key_action("git", True, ord("s")), "char:s")
        self.assertEqual(key_action("git", True, ord("t")), "char:t")
        self.assertEqual(key_action("git", True, ord("b")), "char:b")
        self.assertEqual(key_action("git", True, ord("d")), "char:d")
        self.assertEqual(key_action("git", True, ord("k")), "char:k")
        self.assertEqual(key_action("git", True, ord("q")), "char:q")
        self.assertEqual(key_action("git", True, ord(" ")), "char: ")
        self.assertEqual(key_action("git", True, ord("/")), "char:/")

    def test_typing_git_status_while_filtering_yields_full_query(self):
        query = ""
        for c in "git status":
            action = key_action(query, True, ord(c))
            self.assertTrue(action.startswith("char:"), (c, action))
            query += action[len("char:"):]
        self.assertEqual(query, "git status")

    def test_enter_and_escape_stop_filtering(self):
        self.assertEqual(key_action("git", True, 10), "stop_filter")
        self.assertEqual(key_action("git", True, 13), "stop_filter")
        self.assertEqual(key_action("git", True, 27), "cancel_filter")

    def test_escape_quits_outside_filtering(self):
        self.assertEqual(key_action("", False, 27), "quit")

    def test_backspace_always_keeps_its_role(self):
        self.assertEqual(key_action("git", False, curses.KEY_BACKSPACE), "backspace")
        self.assertEqual(key_action("git", True, curses.KEY_BACKSPACE), "backspace")
        self.assertEqual(key_action("git", False, 127), "backspace")
        self.assertEqual(key_action("git", True, 127), "backspace")

    def test_arrow_and_resize_keys_unaffected_by_filtering(self):
        self.assertEqual(key_action("git", False, curses.KEY_UP), "up")
        self.assertEqual(key_action("git", True, curses.KEY_UP), "up")
        self.assertEqual(key_action("git", False, curses.KEY_DOWN), "down")
        self.assertEqual(key_action("git", True, curses.KEY_DOWN), "down")
        self.assertEqual(key_action("git", False, curses.KEY_RESIZE), "resize")
        self.assertEqual(key_action("git", True, curses.KEY_RESIZE), "resize")


class FakeScreen:
    """Just enough of a curses window for the line editor and confirm prompt:
    replays scripted keys and records every line drawn."""

    def __init__(self, keys):
        self.keys = list(keys)
        self.drawn = []

    def getmaxyx(self):
        return (24, 200)

    def addnstr(self, y, x, text, n):
        self.drawn.append(text[:n])

    def refresh(self):
        pass

    def getch(self):
        return self.keys.pop(0)


def keys(text):
    return [ord(c) for c in text]


BACKSPACE = 127
ENTER = 10
ESC = 27


class TestEditBlockedRule(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        env = {"ZSS_HISTFILE": str(self.tmp / "histfile"), "XDG_DATA_HOME": str(self.tmp / "data")}
        self._old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self.p = store.paths()
        entries = [history.Entry(raw=t.encode(), text=t, ts=None, elapsed=None, extended=False)
                   for t in ["printf a", "printenv", "echo hi", "printf b"]]
        self.p.histfile.write_bytes(history.serialize(entries))
        store.block_add("first", self.p)
        store.block_add("printfx", self.p)
        store.block_add("last", self.p)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def texts(self):
        return [e.text for e in store.load(self.p)]

    def test_edit_keeps_position_and_shows_count_before_confirming(self):
        scr = FakeScreen([BACKSPACE] + keys(" ") + [ENTER] + keys("y"))
        status = _edit_blocked_rule(scr, "printfx", self.p)
        self.assertEqual(store.block_list(self.p), ["first", "printf ", "last"])
        self.assertEqual(self.texts(), ["printenv", "echo hi"])
        self.assertEqual(status, "rule updated; removed 2 entries")
        self.assertTrue(any('Change "printfx" to "printf "? 2 matching history entries will be deleted.' in d
                            for d in scr.drawn), scr.drawn)

    def test_declining_confirmation_changes_nothing(self):
        scr = FakeScreen([BACKSPACE, ENTER] + keys("n"))
        self.assertEqual(_edit_blocked_rule(scr, "printfx", self.p), "cancelled")
        self.assertEqual(store.block_list(self.p), ["first", "printfx", "last"])
        self.assertEqual(len(self.texts()), 4)

    def test_escape_and_unchanged_are_no_ops(self):
        self.assertEqual(_edit_blocked_rule(FakeScreen([ESC]), "printfx", self.p), "unchanged")
        self.assertEqual(_edit_blocked_rule(FakeScreen([ENTER]), "printfx", self.p), "unchanged")
        self.assertEqual(store.block_list(self.p), ["first", "printfx", "last"])

    def test_rejects_trailing_backslash_and_existing_rule(self):
        status = _edit_blocked_rule(FakeScreen(keys("\\") + [ENTER]), "printfx", self.p)
        self.assertIn("backslash", status)
        scr = FakeScreen([BACKSPACE] * len("printfx") + keys("last") + [ENTER])
        self.assertEqual(_edit_blocked_rule(scr, "printfx", self.p), "that prefix is already blocked")
        self.assertEqual(store.block_list(self.p), ["first", "printfx", "last"])


if __name__ == "__main__":
    unittest.main()
