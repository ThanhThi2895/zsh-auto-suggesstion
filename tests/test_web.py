import json
import os
import shutil
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from zss import colors, history, store, web


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WebTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._old = {
            "ZSS_HISTFILE": os.environ.get("ZSS_HISTFILE"),
            "XDG_DATA_HOME": os.environ.get("XDG_DATA_HOME"),
        }
        os.environ["ZSS_HISTFILE"] = str(self.tmp / "histfile")
        os.environ["XDG_DATA_HOME"] = str(self.tmp / "data")
        self.p = store.paths()

        entries = []
        for t in ["git status", "echo hi", "git status"]:
            entries.append(history.Entry(raw=t.encode(), text=t, ts=None, elapsed=None, extended=False))
        self.p.histfile.write_bytes(history.serialize(entries))

        self.port = _free_port()
        web._TOKEN = "test-token"
        web._ALLOWED_HOSTS = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        from http.server import ThreadingHTTPServer

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), web.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def request(self, path, method="GET", body=None, token="test-token", host=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if token is not None:
            req.add_header("X-ZSS-Token", token)
        if host is not None:
            req.add_header("Host", host)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())


class TestAuth(WebTestCase):
    def test_missing_token_rejected(self):
        status, _ = self.request("/api/commands", token=None)
        self.assertEqual(status, 403)

    def test_wrong_token_rejected(self):
        status, _ = self.request("/api/commands", token="nope")
        self.assertEqual(status, 403)

    def test_correct_token_accepted(self):
        status, data = self.request("/api/commands")
        self.assertEqual(status, 200)
        self.assertIn("commands", data)


class TestCommandsAndDelete(WebTestCase):
    def test_list_pagination_and_counts(self):
        status, data = self.request("/api/commands?limit=1")
        self.assertEqual(status, 200)
        self.assertEqual(data["total"], 2)
        self.assertEqual(len(data["commands"]), 1)

    def test_delete_removes_and_backs_up(self):
        status, data = self.request("/api/delete", method="POST", body={"commands": ["git status"], "match": "exact"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["removed"], 2)
        self.assertIsNotNone(data["backup_id"])
        status2, data2 = self.request("/api/commands")
        texts = {c["text"] for c in data2["commands"]}
        self.assertNotIn("git status", texts)

    def test_non_numeric_offset_returns_400(self):
        status, data = self.request("/api/commands?offset=not-a-number")
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_non_numeric_limit_returns_400(self):
        status, data = self.request("/api/commands?limit=not-a-number")
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_unknown_match_mode_returns_400(self):
        status, data = self.request(
            "/api/delete", method="POST", body={"commands": ["git status"], "match": "bogus"}
        )
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_invalid_regex_returns_400(self):
        status, data = self.request(
            "/api/delete", method="POST", body={"commands": ["("], "match": "regex"}
        )
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_non_object_body_returns_400_instead_of_dropping_connection(self):
        # `body.get(...)` on a non-dict JSON body (e.g. a bare list) used to
        # raise AttributeError uncaught, dropping the connection instead of
        # responding with 400.
        status, data = self.request("/api/delete", method="POST", body=[])
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_string_commands_returns_400_instead_of_deleting_chars(self):
        # A string "commands" value used to pass the `if not commands` check
        # and reach store._matcher, which built a set of its characters and
        # deleted any history entry matching one of them.
        status, data = self.request("/api/delete", method="POST", body={"commands": "git status", "match": "exact"})
        self.assertEqual(status, 400)
        self.assertIn("error", data)
        status2, data2 = self.request("/api/commands")
        texts = {c["text"] for c in data2["commands"]}
        self.assertIn("git status", texts)

    def test_non_string_command_items_returns_400(self):
        status, data = self.request("/api/delete", method="POST", body={"commands": ["git status", 1], "match": "exact"})
        self.assertEqual(status, 400)
        self.assertIn("error", data)


class TestBlocklist(WebTestCase):
    def test_delete_does_not_block(self):
        # Deleting history entries must never add a block rule; blocking is
        # manual only (via /api/blocklist POST).
        self.request("/api/delete", method="POST", body={"commands": ["echo hi"], "match": "exact"})
        status, data = self.request("/api/blocklist")
        self.assertEqual(status, 200)
        self.assertEqual(data["blocklist"], [])

    def test_preview_counts_matching_entries(self):
        status, data = self.request("/api/blocklist/preview?text=git")
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 2)

    def test_add_blocks_and_purges_matching_entries(self):
        status, data = self.request("/api/blocklist", method="POST", body={"text": "git"})
        self.assertEqual(status, 200, data)
        self.assertTrue(data["added"])
        self.assertEqual(data["removed"], 2)
        self.assertIsNotNone(data["backup_id"])

        status2, data2 = self.request("/api/blocklist")
        self.assertIn("git", data2["blocklist"])

        status3, data3 = self.request("/api/commands")
        texts = {c["text"] for c in data3["commands"]}
        self.assertNotIn("git status", texts)

    def test_list_and_delete_rule(self):
        self.request("/api/blocklist", method="POST", body={"text": "echo hi"})
        status, data = self.request("/api/blocklist")
        self.assertEqual(status, 200)
        self.assertIn("echo hi", data["blocklist"])

        status2, data2 = self.request("/api/blocklist?text=echo+hi", method="DELETE")
        self.assertEqual(status2, 200)
        self.assertTrue(data2["removed"])

    def test_add_trailing_backslash_returns_400(self):
        status, data = self.request("/api/blocklist", method="POST", body={"text": "echo \\"})
        self.assertEqual(status, 400)
        self.assertIn("backslash", data["error"])
        self.assertEqual(store.block_list(self.p), [])

    def test_add_at_position(self):
        store.block_add("a", self.p)
        store.block_add("c", self.p)
        status, data = self.request("/api/blocklist", method="POST", body={"text": "b", "position": 1})
        self.assertEqual(status, 200, data)
        self.assertTrue(data["added"])
        self.assertEqual(store.block_list(self.p), ["a", "b", "c"])

    def test_add_reports_purge_count_at_position(self):
        store.block_add("a", self.p)
        status, data = self.request("/api/blocklist", method="POST", body={"text": "git", "position": 0})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["removed"], 2)
        self.assertIsNotNone(data["backup_id"])
        self.assertEqual(store.block_list(self.p), ["git", "a"])

    def test_add_rejects_bad_position(self):
        for bad in (-1, "1", 1.5, True):
            status, _ = self.request("/api/blocklist", method="POST", body={"text": "b", "position": bad})
            self.assertEqual(status, 400, bad)
        self.assertEqual(store.block_list(self.p), [])

    def test_edit_rule_in_place_and_purges_new_prefix(self):
        store.block_add("aaa", self.p)
        store.block_add("echo hi there", self.p)
        status, data = self.request("/api/blocklist/edit", method="POST", body={"old": "echo hi there", "new": "echo"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["removed"], 1)
        self.assertIsNotNone(data["backup_id"])
        self.assertEqual(store.block_list(self.p), ["aaa", "echo"])
        texts = {e.text for e in store.load(self.p)}
        self.assertEqual(texts, {"git status"})

    def test_edit_errors(self):
        store.block_add("aaa", self.p)
        status, _ = self.request("/api/blocklist/edit", method="POST", body={"old": "nope", "new": "x"})
        self.assertEqual(status, 404)
        status, _ = self.request("/api/blocklist/edit", method="POST", body={"old": "aaa", "new": "x\\"})
        self.assertEqual(status, 400)
        status, _ = self.request("/api/blocklist/edit", method="POST", body={"old": "aaa"})
        self.assertEqual(status, 400)
        status, _ = self.request("/api/blocklist/edit", method="POST", body={"old": "aaa", "new": "x"}, token=None)
        self.assertEqual(status, 403)
        self.assertEqual(store.block_list(self.p), ["aaa"])


class TestBackups(WebTestCase):
    def test_restore(self):
        original = self.p.histfile.read_bytes()
        self.request("/api/delete", method="POST", body={"commands": ["echo hi"], "match": "exact"})
        status, data = self.request("/api/backups")
        self.assertEqual(status, 200)
        backup_id = data["backups"][0]
        status2, data2 = self.request("/api/backups/restore", method="POST", body={"id": backup_id})
        self.assertEqual(status2, 200, data2)
        self.assertEqual(self.p.histfile.read_bytes(), original)

    def test_non_object_body_returns_400_instead_of_dropping_connection(self):
        status, data = self.request("/api/backups/restore", method="POST", body=[])
        self.assertEqual(status, 400)
        self.assertIn("error", data)


class TestColors(WebTestCase):
    def test_get_returns_defaults_when_no_file(self):
        status, data = self.request("/api/colors")
        self.assertEqual(status, 200)
        self.assertEqual(data["current"], colors.DEFAULTS)

    def test_post_valid_saves(self):
        status, data = self.request("/api/colors", method="POST", body={"command": "fg=42"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["current"]["command"], "fg=42")

    def test_post_invalid_400_and_file_unchanged(self):
        status, data = self.request("/api/colors", method="POST", body={"command": "fg=999"})
        self.assertEqual(status, 400)
        self.assertFalse(self.p.colors_file.exists())

    def test_post_non_string_value_returns_400_instead_of_500(self):
        # A non-string JSON value (e.g. a number or list) used to reach
        # `style.strip()` inside colors.validate() and raise an uncaught
        # AttributeError, which drops the connection instead of a 400.
        status, data = self.request("/api/colors", method="POST", body={"command": 123})
        self.assertEqual(status, 400)
        self.assertIn("error", data)
        self.assertFalse(self.p.colors_file.exists())

    def test_post_normalizes_spaced_style_before_saving(self):
        # colors.validate() strips whitespace per item before checking it,
        # so this must be saved in its normalized (unspaced) form; otherwise
        # the zsh-side validator, which rejects spaces, would disagree with
        # the CLI/web UI about whether the style took effect.
        status, data = self.request("/api/colors", method="POST", body={"command": "fg=red, bold"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["current"]["command"], "fg=red,bold")
        text = self.p.colors_file.read_text()
        self.assertIn("command=fg=red,bold", text)

    def test_reset(self):
        self.request("/api/colors", method="POST", body={"command": "fg=42"})
        status, data = self.request("/api/colors/reset", method="POST", body={"key": "command"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["current"]["command"], colors.DEFAULTS["command"])



class TestStaticPage(WebTestCase):
    """The page is plain HTML/JS with no build step; check it talks to the
    real endpoints rather than any stand-in backend."""

    def setUp(self):
        super().setUp()
        self.html = (web.STATIC_DIR / "index.html").read_text(encoding="utf-8")

    def test_uses_fetch_with_token_header(self):
        self.assertIn("fetch(url", self.html)
        self.assertIn('"X-ZSS-Token": TOKEN', self.html)

    def test_no_prototype_only_code(self):
        for marker in ("mock", "data-scenario", "SEED", "Prototype"):
            self.assertNotIn(marker, self.html)

    def test_enter_does_not_hijack_focused_controls(self):
        self.assertIn('if (e.key === "Enter" && e.target.closest("button, a, input, select, summary")) return;', self.html)

    def test_stale_row_cursor_is_clamped_and_guarded(self):
        self.assertIn("h.cursor = Math.min(h.cursor, h.items.length - 1);", self.html)
        self.assertIn("b.cursor = Math.min(b.cursor, b.items.length - 1);", self.html)
        self.assertIn("if (cur === undefined) return;", self.html)

    def test_history_load_ignores_stale_responses(self):
        self.assertIn("if (my !== histToken) return;", self.html)

    def test_undo_unblock_restores_position_and_confirms_purge(self):
        self.assertIn('api("POST", "/api/blocklist", { text: t, position: pos < 0 ? undefined : pos })', self.html)
        self.assertIn('title: "Block this rule again?"', self.html)

    def test_no_favicon_request(self):
        self.assertIn('<link rel="icon" href="data:,">', self.html)

    def test_rule_edit_uses_in_place_endpoint(self):
        self.assertIn('api("POST", "/api/blocklist/edit", { old: orig, new: v })', self.html)

    def test_page_served_with_token(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/?t=test-token") as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read().decode("utf-8"), self.html)


if __name__ == "__main__":
    unittest.main()
