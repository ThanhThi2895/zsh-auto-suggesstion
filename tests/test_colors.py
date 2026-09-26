import os
import shutil
import tempfile
import unittest
from pathlib import Path

from zss import colors, store


class ColorsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._old = {
            "ZSS_HISTFILE": os.environ.get("ZSS_HISTFILE"),
            "XDG_DATA_HOME": os.environ.get("XDG_DATA_HOME"),
        }
        os.environ["ZSS_HISTFILE"] = str(self.tmp / "histfile")
        os.environ["XDG_DATA_HOME"] = str(self.tmp / "data")
        self.p = store.paths()

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestValidate(unittest.TestCase):
    def test_valid_styles(self):
        for style in ["none", "fg=red", "bg=blue", "bold", "underline", "standout",
                      "fg=red,bold", "fg=123", "fg=255", "fg=#ff00aa", "fg=default,bg=black,underline"]:
            self.assertTrue(colors.validate(style), style)

    def test_invalid_styles(self):
        for style in ["", "fg=", "fg=notacolor", "fg=256", "fg=999", "bogus",
                      "fg=#gggggg", "fg=red,", ",bold", "fg=red bold"]:
            self.assertFalse(colors.validate(style), style)

    def test_non_string_values_are_invalid(self):
        # A JSON body handed straight to validate() (as the web API does)
        # can carry a number, list, or None instead of a string; these must
        # be rejected, not crash on `.strip()`.
        for style in [123, None, ["fg=red"], {"fg": "red"}]:
            self.assertFalse(colors.validate(style), style)


class TestLoadSaveReset(ColorsTestCase):
    def test_load_missing_file_returns_defaults(self):
        self.assertEqual(colors.load(self.p), colors.DEFAULTS)

    def test_save_and_load_round_trip(self):
        colors.save({"command": "fg=42", "unknown": "bg=blue"}, self.p)
        loaded = colors.load(self.p)
        self.assertEqual(loaded["command"], "fg=42")
        self.assertEqual(loaded["unknown"], "bg=blue")
        # untouched keys keep their defaults
        self.assertEqual(loaded["alias"], colors.DEFAULTS["alias"])

    def test_save_rejects_unknown_key(self):
        with self.assertRaises(ValueError):
            colors.save({"not_a_real_key": "fg=red"}, self.p)
        self.assertEqual(colors.load(self.p), colors.DEFAULTS)

    def test_save_rejects_invalid_value_and_writes_nothing(self):
        with self.assertRaises(ValueError):
            colors.save({"command": "fg=999"}, self.p)
        self.assertEqual(colors.load(self.p), colors.DEFAULTS)

    def test_reset_one_key(self):
        colors.save({"command": "fg=42", "alias": "bg=blue"}, self.p)
        colors.reset("command", self.p)
        loaded = colors.load(self.p)
        self.assertEqual(loaded["command"], colors.DEFAULTS["command"])
        self.assertEqual(loaded["alias"], "bg=blue")

    def test_reset_all(self):
        colors.save({"command": "fg=42"}, self.p)
        colors.reset(None, self.p)
        self.assertEqual(colors.load(self.p), colors.DEFAULTS)

    def test_save_normalizes_spaces_around_items(self):
        # validate() strips whitespace per item before checking it, but
        # save() used to persist the raw, unstripped value; the zsh-side
        # validator rejects spaces, so the two sides disagreed about whether
        # a style like "fg=red, bold" was actually in effect.
        colors.save({"command": "fg=red, bold"}, self.p)
        self.assertEqual(colors.load(self.p)["command"], "fg=red,bold")
        text = self.p.colors_file.read_text()
        self.assertIn("command=fg=red,bold", text)
        self.assertNotIn(", bold", text)

    def test_ignores_invalid_lines_in_file(self):
        self.p.data_dir.mkdir(parents=True, exist_ok=True)
        self.p.colors_file.write_text("command=fg=42\nbogus line\nalias=fg=999\n# comment\n")
        loaded = colors.load(self.p)
        self.assertEqual(loaded["command"], "fg=42")
        self.assertEqual(loaded["alias"], colors.DEFAULTS["alias"])


if __name__ == "__main__":
    unittest.main()
