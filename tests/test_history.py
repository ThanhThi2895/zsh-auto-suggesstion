import os
import unittest
from pathlib import Path

from zss import history

FIXTURES = Path(__file__).parent / "fixtures"
REAL_HISTFILE = Path.home() / ".zsh_history"


class TestMetafy(unittest.TestCase):
    def test_round_trip_all_bytes(self):
        data = bytes(range(256))
        self.assertEqual(history.unmetafy(history.metafy(data)), data)

    def test_metafies_only_reserved_range_and_nul(self):
        encoded = history.metafy(bytes([0x00, 0x83, 0xA2, 0xA3, 0x41, 0xC3]))
        # 0x00, 0x83, 0xA2 are in the reserved range; 0xA3, 0x41 ('A'), 0xC3 are not.
        self.assertEqual(encoded, bytes([0x83, 0x20, 0x83, 0xA3, 0x83, 0x82, 0xA3, 0x41, 0xC3]))


class TestParseSerialize(unittest.TestCase):
    def _round_trip(self, path: Path):
        data = path.read_bytes()
        entries = history.parse(data)
        self.assertEqual(history.serialize(entries), data)
        return entries

    def test_plain_fixture_round_trip(self):
        entries = self._round_trip(FIXTURES / "history-plain.txt")
        self.assertEqual(len(entries), 5)
        self.assertFalse(any(e.extended for e in entries))

    def test_extended_fixture_round_trip(self):
        entries = self._round_trip(FIXTURES / "history-extended.txt")
        self.assertEqual(len(entries), 4)
        self.assertTrue(all(e.extended for e in entries))
        self.assertTrue(all(e.ts and e.ts > 0 for e in entries))

    def test_multiline_entry_decoded_with_real_newline(self):
        entries = history.parse((FIXTURES / "history-plain.txt").read_bytes())
        multiline = [e for e in entries if "\n" in e.text]
        self.assertEqual(len(multiline), 1)
        self.assertEqual(multiline[0].text, "echo line1 \nline2 continued")

    def test_vietnamese_entry_decoded(self):
        entries = history.parse((FIXTURES / "history-plain.txt").read_bytes())
        texts = [e.text for e in entries]
        self.assertIn("echo xin chào các bạn", texts)

    def test_empty_input(self):
        self.assertEqual(history.parse(b""), [])
        self.assertEqual(history.serialize([]), b"")

    @unittest.skipUnless(REAL_HISTFILE.exists(), "no real ~/.zsh_history on this machine")
    def test_round_trip_real_history_copy(self):
        # Read-only use of the real file; never written back to it.
        data = REAL_HISTFILE.read_bytes()
        entries = history.parse(data)
        self.assertEqual(history.serialize(entries), data)

    def test_continuation_with_literal_trailing_backslash_stays_one_entry(self):
        # zsh always appends exactly one continuation backslash when writing
        # a multi-line entry. Typing `echo a \` then Enter then `b` makes a
        # real interactive zsh write "echo a \\" (two trailing backslash
        # bytes: the user's literal one plus zsh's own continuation marker),
        # a newline, then "b". Requiring an ODD trailing-backslash count (the
        # bug) misreads the even count here as "not a continuation" and
        # splits this into two bogus entries instead of one.
        data = b"echo a \\\\\nb\n"
        entries = history.parse(data)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].text, "echo a \\\nb")
        self.assertEqual(history.serialize(entries), data)

    def test_split_logical_records_handles_even_trailing_backslashes(self):
        data = b"echo a \\\\\nb\n"
        self.assertEqual(history.split_logical_records(data), [b"echo a \\\\\nb"])


class TestBlockRuleEncoding(unittest.TestCase):
    def test_round_trip_simple(self):
        text = "git status"
        encoded = history.encode_block_rule(text)
        self.assertEqual(history.decode_block_rule(encoded), text)

    def test_round_trip_multiline_and_unicode(self):
        text = "echo line1 \nline2à continued"
        encoded = history.encode_block_rule(text)
        # A real newline must be escaped as backslash-newline (odd trailing
        # backslash), so the whole blob still parses back as ONE record.
        records = history.split_logical_records(encoded + b"\n")
        self.assertEqual(len(records), 1)
        self.assertEqual(history.decode_block_rule(encoded), text)

    def test_two_block_rules_stay_separate_records(self):
        one = history.encode_block_rule("echo multi\nline")
        two = history.encode_block_rule("echo simple")
        blob = one + b"\n" + two + b"\n"
        records = history.split_logical_records(blob)
        self.assertEqual(len(records), 2)
        self.assertEqual(history.decode_block_rule(records[0]), "echo multi\nline")
        self.assertEqual(history.decode_block_rule(records[1]), "echo simple")

    def test_trailing_backslash_rule_would_merge_with_next(self):
        # Why store.validate_block_rule rejects a trailing backslash: the
        # encoding can't round-trip it, it swallows the following rule.
        blob = history.encode_block_rule("echo \\") + b"\n" + history.encode_block_rule("ls") + b"\n"
        self.assertEqual(len(history.split_logical_records(blob)), 1)


if __name__ == "__main__":
    unittest.main()
