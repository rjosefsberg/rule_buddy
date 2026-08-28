#!/usr/bin/env python3
"""server.py - the window, drawn by an ordinary browser tab instead of a webview.

WebView2 kept locking up on a fast relaunch, and each lockup left its own
process tree running behind it. A plain HTTP server sidesteps the whole
class of problem: Python owns no window at all, whatever browser the OS opens
owns its own tab, and closing that tab cannot orphan anything of ours.

    python -m rulebuddy.server

Every method on shell.Api becomes one POST to /api/<name>, its arguments a
JSON array in the body, its return value JSON in the response. app.js's
api() wrapper turns that back into the same-looking calls the page always
made, so the page itself barely changed.
"""

import json
import mimetypes
import os
import socket
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import core
from .shell import Api, UI, trace


def free_port():
    """Ask the OS for a port nothing else is using, rather than guess one."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_handler(api):
    """A request handler closed over one Api instance, one collection."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass                     # trace() owns the console instead

        def _json(self, obj, status=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path):
            if not os.path.isfile(path):
                self.send_error(404)
                return
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = self.path.split("?", 1)[0]
            if route == "/":
                route = "/index.html"
            # A route is a name under UI, never a way out of it: strip any
            # leading slash before join so an absolute path in the request
            # cannot replace UI outright, then check the result still lands
            # inside UI once ../ segments are resolved.
            target = os.path.normpath(os.path.join(UI, route.lstrip("/")))
            if os.path.commonpath([target, UI]) != os.path.normpath(UI):
                self.send_error(403)
                return
            self._file(target)

        def do_POST(self):
            if not self.path.startswith("/api/"):
                self.send_error(404)
                return
            name = self.path[len("/api/"):]
            method = getattr(api, name, None)
            if name.startswith("_") or not callable(method):
                self._json({"error": f"No such call: {name}"}, 404)
                return
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"[]"
            try:
                args = json.loads(raw or b"[]")
            except json.JSONDecodeError:
                args = []
            try:
                result = method(*args)
            except Exception as err:
                self._json({"error": f"{type(err).__name__}: {err}"}, 500)
                return
            self._json({"result": result})

    return Handler


def start(db_path=None):
    """Open the tab. Everything else happens in the page."""
    core.load_config()
    path = db_path or core.DB["path"]
    if not os.path.isabs(path):
        path = os.path.join(core.app_dir(), path)
    if not os.path.exists(path):
        sys.exit(f"No index at {path}")

    trace("server: creating Api")
    api = Api(path)
    port = free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(api))
    url = f"http://127.0.0.1:{port}/"
    trace(f"server: listening on {url}")
    webbrowser.open(url)
    print(f"Rule Buddy is open at {url}\nPress Ctrl+C here to stop it.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    start(sys.argv[1] if len(sys.argv) > 1 else None)
