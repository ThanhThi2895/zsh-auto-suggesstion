import multiprocessing
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from zss import history, store


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.histfile = self.tmp / "zsh_history"
        self._env_patch = {
            "ZSS_HISTFILE": str(self.histfile),
            "XDG_DATA_HOME": str(self.tmp / "data"),
        }
        self._old_env = {k: os.environ.get(k) for k in self._env_patch}
        os.environ.update(self._env_patch)

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_history(self, *texts):
        entries = []
        for t in texts:
            raw = t.encode("utf-8").replace(b"\n", b"\\\n")
            entries.append(history.Entry(raw=raw, text=t, ts=None, elapsed=None, extended=False))
        self.histfile.write_bytes(history.serialize(entries))


class TestUniqueCommands(StoreTestCase):
    def test_counts_and_recency(self):
        self.write_history("a", "b", "a", "c")
        p = store.paths()
        cmds = {c.text: c for c in store.unique_commands(store.load(p))}
        self.assertEqual(cmds["a"].count, 2)
        self.assertEqual(cmds["a"].last_index, 2)
        self.assertEqual(cmds["b"].count, 1)
        self.assertEqual(cmds["c"].last_index, 3)


class TestDelete(StoreTestCase):
    def test_exact_delete_removes_all_occurrences_and_backs_up(self):
        self.write_history("keep me", "echo xin chào", "keep me", "other")
        p = store.paths()
        original = self.histfile.read_bytes()

        result = store.delete(["echo xin chào"], match="exact", p=p)

        self.assertEqual(result.removed, 1)
        self.assertIsNotNone(result.backup_id)
        remaining = [e.text for e in store.load(p)]
        self.assertEqual(remaining, ["keep me", "keep me", "other"])

        backup_path = p.backups_dir / result.backup_id
        self.assertEqual(backup_path.read_bytes(), original)

    def test_delete_multiline_entry_leaves_neighbours_byte_exact(self):
        self.write_history("before", "line1\nline2", "after")
        p = store.paths()
        before_raw = store.load(p)[0].raw
        after_raw = store.load(p)[2].raw

        result = store.delete(["line1\nline2"], match="exact", p=p)

        self.assertEqual(result.removed, 1)
        remaining = store.load(p)
        self.assertEqual(len(remaining), 2)
        self.assertEqual(remaining[0].raw, before_raw)
        self.assertEqual(remaining[1].raw, after_raw)

    def test_delete_does_not_add_block_rule(self):
        # Deleting is block-list-independent now: blocking is manual only.
        self.write_history("rm me")
        p = store.paths()
        store.delete(["rm me"], match="exact", p=p)
        self.assertEqual(store.block_list(p), [])

    def test_delete_no_match_returns_zero_and_no_backup(self):
        self.write_history("a")
        p = store.paths()
        result = store.delete(["nope"], match="exact", p=p)
        self.assertEqual(result.removed, 0)
        self.assertIsNone(result.backup_id)
        self.assertEqual(store.list_backups(p), [])

    def test_prefix_and_regex_match(self):
        self.write_history("git status", "git log", "ls -la")
        p = store.paths()
        result = store.delete(["git "], match="prefix", p=p)
        self.assertEqual(result.removed, 2)
        self.write_history("foo123", "bar456", "baz")
        result = store.delete([r"\d+$"], match="regex", p=p)
        self.assertEqual(result.removed, 2)

    def test_writer_holding_fd_since_before_delete_still_lands_in_file(self):
        # zsh keeps its own histfile fd open across the whole shell session
        # for incremental append. `delete()` used to rewrite the histfile by
        # renaming a temp file over it (`os.replace`), which repoints the
        # path at a NEW inode; a writer's fd opened before that call still
        # refers to the OLD inode, so its next append would land nowhere the
        # path can see. Writing in place (same inode, truncate + rewrite)
        # keeps a pre-existing fd valid.
        self.write_history("keep", "delete-me")
        p = store.paths()
        fd = os.open(str(p.histfile), os.O_RDWR)
        try:
            result = store.delete(["delete-me"], match="exact", p=p)
            self.assertEqual(result.removed, 1)

            os.lseek(fd, 0, os.SEEK_END)
            os.write(fd, b"appended-after-delete\n")
            os.fsync(fd)
        finally:
            os.close(fd)

        texts = [e.text for e in store.load(p)]
        self.assertIn("keep", texts)
        self.assertNotIn("delete-me", texts)
        self.assertIn("appended-after-delete", texts)


class TestBackupRestore(StoreTestCase):
    def test_restore_round_trip(self):
        self.write_history("one", "two")
        p = store.paths()
        original = self.histfile.read_bytes()
        result = store.delete(["one"], match="exact", p=p)

        pre_restore_id = store.restore(result.backup_id, p=p)

        self.assertEqual(self.histfile.read_bytes(), original)
        self.assertIsNotNone(pre_restore_id)

    def test_prune_keeps_last_20(self):
        self.write_history("a")
        p = store.paths()
        for _ in range(25):
            store.create_backup(p)
            time.sleep(0.001)
        self.assertLessEqual(len(store.list_backups(p)), store.MAX_BACKUPS)


class TestBlockList(StoreTestCase):
    def test_add_list_remove(self):
        p = store.paths()
        store.block_add("echo hi", p)
        store.block_add("echo xin chào", p)
        self.assertEqual(set(store.block_list(p)), {"echo hi", "echo xin chào"})

        self.assertTrue(store.block_remove("echo hi", p))
        self.assertEqual(store.block_list(p), ["echo xin chào"])
        self.assertFalse(store.block_remove("echo hi", p))

    def test_add_is_idempotent(self):
        p = store.paths()
        store.block_add("dup", p)
        store.block_add("dup", p)
        self.assertEqual(store.block_list(p), ["dup"])

    def test_add_rejects_empty_rule(self):
        p = store.paths()
        with self.assertRaises(ValueError):
            store.block_add("", p)

    def test_add_rejects_trailing_backslash_and_leaves_list_intact(self):
        self.write_history("echo \\ x")
        p = store.paths()
        store.block_add("ls", p)
        with self.assertRaises(ValueError):
            store.block_add("echo \\", p)
        self.assertEqual(store.block_list(p), ["ls"])
        self.assertEqual([e.text for e in store.load(p)], ["echo \\ x"])

    def test_backslash_inside_rule_round_trips(self):
        p = store.paths()
        store.block_add("echo \\n", p)
        store.block_add("ls", p)
        self.assertEqual(store.block_list(p), ["echo \\n", "ls"])

    def test_add_purges_matching_prefix_entries_and_backs_up(self):
        self.write_history("printf foo", "printf bar", "echo keep")
        p = store.paths()
        original = self.histfile.read_bytes()

        result = store.block_add("printf ", p)

        self.assertTrue(result.added)
        self.assertEqual(result.delete_result.removed, 2)
        self.assertIsNotNone(result.delete_result.backup_id)
        remaining = [e.text for e in store.load(p)]
        self.assertEqual(remaining, ["echo keep"])
        backup_path = p.backups_dir / result.delete_result.backup_id
        self.assertEqual(backup_path.read_bytes(), original)

    def test_add_at_position_inserts_there(self):
        p = store.paths()
        for r in ("a", "b", "c"):
            store.block_add(r, p)
        store.block_add("x", p, position=1)
        self.assertEqual(store.block_list(p), ["a", "x", "b", "c"])
        store.block_add("y", p, position=0)
        self.assertEqual(store.block_list(p), ["y", "a", "x", "b", "c"])

    def test_add_position_past_end_appends(self):
        p = store.paths()
        store.block_add("a", p)
        store.block_add("z", p, position=99)
        self.assertEqual(store.block_list(p), ["a", "z"])

    def test_add_position_never_moves_existing_rule(self):
        p = store.paths()
        store.block_add("a", p)
        store.block_add("b", p)
        result = store.block_add("b", p, position=0)
        self.assertFalse(result.added)
        self.assertEqual(store.block_list(p), ["a", "b"])

    def test_add_rejects_negative_position(self):
        p = store.paths()
        store.block_add("a", p)
        with self.assertRaises(ValueError):
            store.block_add("b", p, position=-1)
        self.assertEqual(store.block_list(p), ["a"])

    def test_unblock_then_re_add_at_old_position_restores_list(self):
        self.write_history("git push origin", "echo keep")
        p = store.paths()
        store.block_add("printfx", p)
        store.block_add("git push", p)
        store.block_add("rm -rf", p)
        before = store.block_list(p)
        pos = before.index("git push")
        store.block_remove("git push", p)
        self.write_history("git push later", "echo keep")

        result = store.block_add("git push", p, position=pos)

        self.assertEqual(store.block_list(p), before)
        self.assertEqual(result.delete_result.removed, 1)
        self.assertIsNotNone(result.delete_result.backup_id)

    def test_re_adding_existing_rule_still_purges_new_matches(self):
        p = store.paths()
        store.block_add("printf ", p)
        self.write_history("printf later")

        result = store.block_add("printf ", p)

        self.assertFalse(result.added)
        self.assertEqual(result.delete_result.removed, 1)
        self.assertEqual(store.block_list(p), ["printf "])

    def test_prefix_rule_does_not_care_what_follows(self):
        self.write_history("printf", "printf %s\n", "printfoo")
        p = store.paths()
        result = store.block_add("printf", p)
        self.assertEqual(result.delete_result.removed, 3)


class TestBlockEdit(StoreTestCase):
    def test_edit_replaces_in_place_and_purges_new_prefix(self):
        p = store.paths()
        store.block_add("aaa", p)
        store.block_add("printf foo", p)
        store.block_add("zzz", p)
        self.write_history("printf bar", "printf foo 1", "echo keep")
        original = self.histfile.read_bytes()

        result = store.block_edit("printf foo", "printf ", p)

        self.assertTrue(result.found)
        self.assertEqual(store.block_list(p), ["aaa", "printf ", "zzz"])
        self.assertEqual(result.delete_result.removed, 2)
        self.assertEqual([e.text for e in store.load(p)], ["echo keep"])
        backup_path = p.backups_dir / result.delete_result.backup_id
        self.assertEqual(backup_path.read_bytes(), original)

    def test_edit_missing_rule_changes_nothing(self):
        p = store.paths()
        store.block_add("aaa", p)
        self.write_history("printf x")
        result = store.block_edit("nope", "printf", p)
        self.assertFalse(result.found)
        self.assertEqual(result.delete_result.removed, 0)
        self.assertEqual(store.block_list(p), ["aaa"])
        self.assertEqual(len(store.load(p)), 1)

    def test_edit_to_existing_rule_drops_duplicate(self):
        p = store.paths()
        store.block_add("a", p)
        store.block_add("b", p)
        store.block_edit("a", "b", p)
        self.assertEqual(store.block_list(p), ["b"])

    def test_edit_rejects_invalid_new_rule(self):
        p = store.paths()
        store.block_add("a", p)
        for bad in ("", "echo \\"):
            with self.assertRaises(ValueError):
                store.block_edit("a", bad, p)
        self.assertEqual(store.block_list(p), ["a"])

    def test_edit_multiline_rule(self):
        p = store.paths()
        store.block_add("echo a\nb", p)
        store.block_add("ls", p)
        store.block_edit("echo a\nb", "echo c\nd", p)
        self.assertEqual(store.block_list(p), ["echo c\nd", "ls"])


class TestCountMatching(StoreTestCase):
    def test_counts_without_deleting(self):
        self.write_history("git status", "git log", "ls -la")
        p = store.paths()
        self.assertEqual(store.count_matching(["git "], match="prefix", p=p), 2)
        self.assertEqual(len(store.load(p)), 3)


def _child_append(env_updates, text):
    os.environ.update(env_updates)
    from zss import history, store as _store

    with _store.history_lock() as p:
        entries = history.parse(p.histfile.read_bytes())
        raw = text.encode("utf-8")
        entries.append(history.Entry(raw=raw, text=text, ts=None, elapsed=None, extended=False))
        _store._atomic_write(p.histfile, history.serialize(entries))


class TestConcurrency(StoreTestCase):
    def test_child_process_append_survives_concurrent_delete(self):
        self.write_history("keep", "delete-me")
        p = store.paths()
        env_updates = {k: os.environ[k] for k in ("ZSS_HISTFILE", "XDG_DATA_HOME")}

        proc = multiprocessing.Process(target=_child_append, args=(env_updates, "child appended"))
        proc.start()
        result = store.delete(["delete-me"], match="exact", p=p)
        proc.join(timeout=5)

        self.assertEqual(proc.exitcode, 0)
        self.assertEqual(result.removed, 1)
        texts = [e.text for e in store.load(p)]
        self.assertIn("child appended", texts)
        self.assertIn("keep", texts)
        self.assertNotIn("delete-me", texts)

    def test_lock_timeout_raises_and_leaves_no_partial_file(self):
        self.write_history("a")
        p = store.paths()
        original = self.histfile.read_bytes()
        with store.history_lock(p):
            with self.assertRaises(store.LockTimeout):
                with store.history_lock(p, timeout=0.2):
                    pass
        self.assertEqual(self.histfile.read_bytes(), original)
        self.assertFalse(Path(str(p.histfile) + ".zss-tmp").exists())


if __name__ == "__main__":
    unittest.main()
