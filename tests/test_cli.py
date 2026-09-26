import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = dict(os.environ)
        self.env["ZSS_HISTFILE"] = str(self.tmp / "histfile")
        self.env["XDG_DATA_HOME"] = str(self.tmp / "data")
        self.env["PYTHONPATH"] = str(REPO_ROOT)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_history(self, *texts):
        from zss import history

        entries = []
        for t in texts:
            raw = t.encode("utf-8").replace(b"\n", b"\\\n")
            entries.append(history.Entry(raw=raw, text=t, ts=None, elapsed=None, extended=False))
        Path(self.env["ZSS_HISTFILE"]).write_bytes(history.serialize(entries))

    def run_cli(self, *args, input_text=None):
        return subprocess.run(
            [sys.executable, "-m", "zss", *args],
            env=self.env,
            capture_output=True,
            text=True,
            input=input_text,
            cwd=REPO_ROOT,
        )


class TestList(CliTestCase):
    def test_list_json_counts(self):
        self.write_history("a", "b", "a")
        result = self.run_cli("list", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        counts = {d["text"]: d["count"] for d in data}
        self.assertEqual(counts["a"], 2)
        self.assertEqual(counts["b"], 1)

    def test_list_grep_filters(self):
        self.write_history("git status", "ls -la")
        result = self.run_cli("list", "--json", "--grep", "git")
        data = json.loads(result.stdout)
        self.assertEqual([d["text"] for d in data], ["git status"])


class TestRm(CliTestCase):
    def test_dry_run_changes_nothing(self):
        self.write_history("a", "b")
        result = self.run_cli("rm", "--dry-run", "-y", "a")
        self.assertEqual(result.returncode, 0, result.stderr)
        result2 = self.run_cli("list", "--json")
        data = json.loads(result2.stdout)
        self.assertEqual({d["text"] for d in data}, {"a", "b"})

    def test_yes_deletes_backs_up_and_does_not_block(self):
        self.write_history("a", "b")
        result = self.run_cli("rm", "-y", "a")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("removed 1", result.stdout)

        result2 = self.run_cli("list", "--json")
        data = json.loads(result2.stdout)
        self.assertEqual({d["text"] for d in data}, {"b"})

        backups = self.run_cli("backup", "list")
        self.assertTrue(backups.stdout.strip())

        # Deleting is block-list-independent: blocking is manual only.
        blocklist = self.run_cli("block", "list")
        self.assertEqual(blocklist.stdout.strip(), "")

    def test_no_match_exit_code_1(self):
        self.write_history("a")
        result = self.run_cli("rm", "-y", "nope")
        self.assertEqual(result.returncode, 1)

    def test_prompts_without_yes_and_aborts_on_no(self):
        self.write_history("a")
        result = self.run_cli("rm", "a", input_text="n\n")
        self.assertNotEqual(result.returncode, 0)
        result2 = self.run_cli("list", "--json")
        data = json.loads(result2.stdout)
        self.assertEqual([d["text"] for d in data], ["a"])


class TestBlock(CliTestCase):
    def test_add_blocks_and_purges_matching_entries(self):
        self.write_history("printf foo", "printf bar", "echo keep")
        result = self.run_cli("block", "add", "-y", "printf ")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("removed 2", result.stdout)

        listing = self.run_cli("list", "--json")
        data = json.loads(listing.stdout)
        self.assertEqual({d["text"] for d in data}, {"echo keep"})

        blocklist = self.run_cli("block", "list")
        self.assertIn("printf ", blocklist.stdout)

    def test_add_prompts_without_yes_and_aborts_on_no(self):
        self.write_history("printf foo")
        result = self.run_cli("block", "add", "printf ", input_text="n\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.run_cli("block", "list").stdout.strip(), "")
        listing = self.run_cli("list", "--json")
        self.assertEqual(len(json.loads(listing.stdout)), 1)

    def test_rm_unblocks(self):
        self.write_history("a")
        result = self.run_cli("block", "rm", "a")
        self.assertEqual(result.returncode, 1)
        self.run_cli("block", "add", "-y", "a")
        result2 = self.run_cli("block", "rm", "a")
        self.assertEqual(result2.returncode, 0, result2.stderr)
        result3 = self.run_cli("block", "list")
        self.assertEqual(result3.stdout.strip(), "")

    def test_add_rejects_trailing_backslash_before_prompting(self):
        self.write_history("echo \\ x")
        result = self.run_cli("block", "add", "echo \\", input_text="y\n")
        self.assertEqual(result.returncode, 2)
        self.assertIn("backslash", result.stderr)
        self.assertNotIn("Block", result.stdout)
        self.assertEqual(self.run_cli("block", "list").stdout.strip(), "")

    def test_add_preview_singular_grammar(self):
        self.write_history("printf foo")
        result = self.run_cli("block", "add", "printf ", input_text="n\n")
        self.assertIn("1 history entry starts with this", result.stdout)

    def test_edit_replaces_rule_and_purges_new_prefix(self):
        self.run_cli("block", "add", "-y", "printf foo")
        self.write_history("printf bar", "echo keep")
        result = self.run_cli("block", "edit", "-y", "printf foo", "printf ")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("removed 1 entry", result.stdout)
        self.assertEqual(self.run_cli("block", "list").stdout, "printf \n")
        data = json.loads(self.run_cli("list", "--json").stdout)
        self.assertEqual([d["text"] for d in data], ["echo keep"])

    def test_edit_prompts_and_aborts_on_no(self):
        self.run_cli("block", "add", "-y", "a")
        result = self.run_cli("block", "edit", "a", "b", input_text="n\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.run_cli("block", "list").stdout.strip(), "a")

    def test_edit_missing_or_invalid(self):
        self.run_cli("block", "add", "-y", "a")
        missing = self.run_cli("block", "edit", "-y", "nope", "b")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("not in the block list", missing.stderr)
        bad = self.run_cli("block", "edit", "-y", "a", "b\\")
        self.assertEqual(bad.returncode, 2)
        self.assertEqual(self.run_cli("block", "list").stdout.strip(), "a")


class TestBackup(CliTestCase):
    def test_restore_round_trip(self):
        self.write_history("a", "b")
        original = Path(self.env["ZSS_HISTFILE"]).read_bytes()
        self.run_cli("rm", "-y", "a")

        backups = self.run_cli("backup", "list").stdout.strip().splitlines()
        self.assertTrue(backups)
        result = self.run_cli("backup", "restore", backups[0])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(self.env["ZSS_HISTFILE"]).read_bytes(), original)


class TestColors(CliTestCase):
    def test_set_valid_and_invalid(self):
        ok = self.run_cli("colors", "set", "command", "fg=42")
        self.assertEqual(ok.returncode, 0, ok.stderr)

        bad = self.run_cli("colors", "set", "command", "fg=999")
        self.assertEqual(bad.returncode, 2)

    def test_reset(self):
        self.run_cli("colors", "set", "command", "fg=42")
        result = self.run_cli("colors", "reset", "command")
        self.assertEqual(result.returncode, 0, result.stderr)
        listing = self.run_cli("colors", "list").stdout
        self.assertIn("command=fg=green", listing)


if __name__ == "__main__":
    unittest.main()
