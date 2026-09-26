"""Local-only web UI: stdlib http.server, a random per-run token, and a
strict Host/Origin check so nothing but the page it just printed can drive
the delete/restore/colour-write endpoints.
"""

import json
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from zss import colors, history, store

STATIC_DIR = Path(__file__).parent / "web"
_TOKEN = ""
_ALLOWED_HOSTS = set()
_last_request_time = [time.time()]
_IDLE_EXIT_SECONDS = None


def _parse_idle_exit(spec):
    if not spec:
        return None
    m = re.match(r"^(\d+)([smh]?)$", spec.strip())
    if not m:
        raise ValueError(f"invalid --idle-exit value: {spec!r}")
    n = int(m.group(1))
    unit = m.group(2) or "s"
    return n * {"s": 1, "m": 60, "h": 3600}[unit]


class Handler(BaseHTTPRequestHandler):
    server_version = "zss-web/1"

    def log_message(self, fmt, *args):
        pass  # keep stdout clean; the printed URL is the only chrome this needs

    # -- shared helpers ----------------------------------------------------

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type):
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):
        host = self.headers.get("Host", "")
        return host in _ALLOWED_HOSTS

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True  # not all browsers send Origin for same-origin GET/navigation
        try:
            parts = urlsplit(origin)
        except ValueError:
            return False
        netloc = parts.netloc
        return netloc in _ALLOWED_HOSTS

    def _token_ok(self, query):
        if self.headers.get("X-ZSS-Token") == _TOKEN:
            return True
        # allow the initial page load to carry the token as a query param
        return query.get("t", [None])[0] == _TOKEN

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    # -- routing -------------------------------------------------------------

    def do_GET(self):
        _last_request_time[0] = time.time()
        if not self._host_ok():
            self._send_json(403, {"error": "bad host"})
            return
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path

        if path == "/" or path == "/index.html":
            if query.get("t", [None])[0] != _TOKEN:
                self._send_json(403, {"error": "missing or bad token"})
                return
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return

        if not path.startswith("/api/"):
            self._send_json(404, {"error": "not found"})
            return
        if not self._token_ok(query) or not self._origin_ok():
            self._send_json(403, {"error": "forbidden"})
            return

        if path == "/api/commands":
            self._api_commands(query)
        elif path == "/api/blocklist":
            self._api_blocklist_list()
        elif path == "/api/blocklist/preview":
            self._api_blocklist_preview(query)
        elif path == "/api/backups":
            self._api_backups_list()
        elif path == "/api/colors":
            self._api_colors_get()
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        _last_request_time[0] = time.time()
        if not self._host_ok():
            self._send_json(403, {"error": "bad host"})
            return
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path
        if not self._token_ok(query) or not self._origin_ok():
            self._send_json(403, {"error": "forbidden"})
            return
        try:
            body = self._read_json_body()
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "invalid JSON body"})
            return

        if path == "/api/delete":
            self._api_delete(body)
        elif path == "/api/blocklist":
            self._api_blocklist_add(body)
        elif path == "/api/blocklist/edit":
            self._api_blocklist_edit(body)
        elif path == "/api/backups/restore":
            self._api_backups_restore(body)
        elif path == "/api/colors":
            self._api_colors_set(body)
        elif path == "/api/colors/reset":
            self._api_colors_reset(body)
        else:
            self._send_json(404, {"error": "not found"})

    def do_DELETE(self):
        _last_request_time[0] = time.time()
        if not self._host_ok():
            self._send_json(403, {"error": "bad host"})
            return
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        if not self._token_ok(query) or not self._origin_ok():
            self._send_json(403, {"error": "forbidden"})
            return
        if parsed.path == "/api/blocklist":
            self._api_blocklist_delete(query)
        else:
            self._send_json(404, {"error": "not found"})

    # -- endpoint implementations --------------------------------------------

    def _api_commands(self, query):
        p = store.paths()
        cmds = store.unique_commands(store.load(p))
        q = query.get("q", [""])[0]
        if q:
            cmds = [c for c in cmds if q in c.text]
        sort = query.get("sort", ["recent"])[0]
        if sort == "count":
            cmds.sort(key=lambda c: (-c.count, -c.last_index))
        else:
            cmds.sort(key=lambda c: -c.last_index)
        try:
            offset = int(query.get("offset", ["0"])[0] or 0)
            limit = int(query.get("limit", ["100"])[0] or 100)
        except ValueError:
            self._send_json(400, {"error": "offset and limit must be integers"})
            return
        page = cmds[offset: offset + limit]
        self._send_json(200, {
            "total": len(cmds),
            "commands": [
                {"text": c.text, "count": c.count, "last_index": c.last_index, "last_ts": c.last_ts}
                for c in page
            ],
        })

    def _api_delete(self, body):
        if not isinstance(body, dict):
            self._send_json(400, {"error": "expected a JSON object body"})
            return
        commands = body.get("commands") or []
        match = body.get("match", "exact")
        if not isinstance(commands, list) or not all(isinstance(c, str) for c in commands):
            self._send_json(400, {"error": "commands must be a list of strings"})
            return
        if not commands:
            self._send_json(400, {"error": "no commands given"})
            return
        p = store.paths()
        try:
            result = store.delete(commands, match=match, p=p)
        except store.LockTimeout as e:
            self._send_json(503, {"error": str(e)})
            return
        except (ValueError, re.error) as e:
            self._send_json(400, {"error": str(e)})
            return
        self._send_json(200, {"removed": result.removed, "backup_id": result.backup_id})

    def _api_blocklist_list(self):
        p = store.paths()
        self._send_json(200, {"blocklist": store.block_list(p)})

    def _api_blocklist_preview(self, query):
        text = query.get("text", [None])[0]
        if not text:
            self._send_json(400, {"error": "missing text"})
            return
        p = store.paths()
        count = store.count_matching([text], match="prefix", p=p)
        self._send_json(200, {"count": count})

    def _api_blocklist_add(self, body):
        if not isinstance(body, dict):
            self._send_json(400, {"error": "expected a JSON object body"})
            return
        text = body.get("text")
        if not isinstance(text, str) or not text:
            self._send_json(400, {"error": "missing text"})
            return
        position = body.get("position")
        if position is not None and (not isinstance(position, int) or isinstance(position, bool) or position < 0):
            self._send_json(400, {"error": "position must be a non-negative integer"})
            return
        p = store.paths()
        try:
            result = store.block_add(text, p, position=position)
        except store.LockTimeout as e:
            self._send_json(503, {"error": str(e)})
            return
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return
        self._send_json(200, {
            "added": result.added,
            "removed": result.delete_result.removed,
            "backup_id": result.delete_result.backup_id,
        })

    def _api_blocklist_edit(self, body):
        if not isinstance(body, dict):
            self._send_json(400, {"error": "expected a JSON object body"})
            return
        old, new = body.get("old"), body.get("new")
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            self._send_json(400, {"error": "expected {old: string, new: string}"})
            return
        p = store.paths()
        try:
            result = store.block_edit(old, new, p)
        except store.LockTimeout as e:
            self._send_json(503, {"error": str(e)})
            return
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return
        if not result.found:
            self._send_json(404, {"error": "that rule is not in the block list"})
            return
        self._send_json(200, {
            "removed": result.delete_result.removed,
            "backup_id": result.delete_result.backup_id,
        })

    def _api_blocklist_delete(self, query):
        text = query.get("text", [None])[0]
        if text is None:
            self._send_json(400, {"error": "missing text"})
            return
        p = store.paths()
        removed = store.block_remove(text, p)
        self._send_json(200, {"removed": removed})

    def _api_backups_list(self):
        p = store.paths()
        self._send_json(200, {"backups": store.list_backups(p)})

    def _api_backups_restore(self, body):
        if not isinstance(body, dict):
            self._send_json(400, {"error": "expected a JSON object body"})
            return
        backup_id = body.get("id")
        if not backup_id:
            self._send_json(400, {"error": "missing id"})
            return
        p = store.paths()
        backup_path = p.backups_dir / backup_id
        if not backup_path.exists() or backup_path.parent != p.backups_dir:
            self._send_json(404, {"error": "no such backup"})
            return
        pre_restore_id = store.restore(backup_id, p)
        self._send_json(200, {"restored": backup_id, "backup_of_previous": pre_restore_id})

    def _api_colors_get(self):
        p = store.paths()
        self._send_json(200, {"current": colors.load(p), "defaults": colors.DEFAULTS})

    def _api_colors_set(self, body):
        p = store.paths()
        if not isinstance(body, dict) or not body:
            self._send_json(400, {"error": "expected {key: style, ...}"})
            return
        try:
            colors.save(body, p)
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return
        self._send_json(200, {"current": colors.load(p)})

    def _api_colors_reset(self, body):
        p = store.paths()
        key = body.get("key") if isinstance(body, dict) else None
        try:
            colors.reset(key, p)
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return
        self._send_json(200, {"current": colors.load(p)})


def _report_port_owner(port):
    try:
        out = subprocess.run(["lsof", "-i", f":{port}"], capture_output=True, text=True, timeout=3)
        if out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def main(port=7799, no_open=False, idle_exit=None):
    global _TOKEN, _ALLOWED_HOSTS, _IDLE_EXIT_SECONDS

    try:
        _IDLE_EXIT_SECONDS = _parse_idle_exit(idle_exit)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    _TOKEN = secrets.token_hex(32)
    _ALLOWED_HOSTS = {f"127.0.0.1:{port}", f"localhost:{port}"}

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        owner = _report_port_owner(port)
        print(f"port {port} is already in use: {e}", file=sys.stderr)
        if owner:
            print(owner, file=sys.stderr)
        return 1

    url = f"http://127.0.0.1:{port}/?t={_TOKEN}"
    print(f"zss web UI: {url}", flush=True)
    print("Ctrl-C to stop.", flush=True)

    if not no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    if _IDLE_EXIT_SECONDS:
        def _idle_watch():
            while True:
                time.sleep(5)
                if time.time() - _last_request_time[0] > _IDLE_EXIT_SECONDS:
                    print("idle timeout reached, exiting")
                    server.shutdown()
                    return

        threading.Thread(target=_idle_watch, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
