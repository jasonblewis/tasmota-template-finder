#!/usr/bin/env python3
"""Zero-dependency local dev server with live reload.

Serves the current directory like `python -m http.server`, but:
  * injects a tiny live-reload script into every HTML page, and
  * pushes a browser refresh (via Server-Sent Events) whenever a watched file
    changes on disk — index.html, *.js, *.css, or tasmota.db.

So editing index.html or re-running `python importer.py` auto-refreshes the tab.

    python devserver.py           # http://localhost:8000
    python devserver.py 9000      # custom port

Standard library only — no pip installs.
"""

import http.server
import os
import queue
import socketserver
import sys
import threading
import time
from pathlib import Path

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
ROOT = Path.cwd()

WATCH_EXT = {".html", ".js", ".css"}
WATCH_EXTRA = {"tasmota.db"}

_clients: list[queue.Queue] = []          # one queue per connected browser tab
_clients_lock = threading.Lock()

RELOAD_SNIPPET = b"""
<script>
(function () {
  try {
    var es = new EventSource("/__reload");
    es.onmessage = function () { location.reload(); };
  } catch (e) { /* live reload unavailable */ }
})();
</script>
"""


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep the console quiet; the watcher prints reloads

    def end_headers(self):
        # Never let the browser cache anything in dev — otherwise a rebuilt
        # tasmota.db can be served stale against a newer index.html.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path.split("?")[0] == "/__reload":
            return self._serve_reload_stream()

        path = self.translate_path(self.path)
        if os.path.isdir(path):
            path = os.path.join(path, "index.html")
        if path.endswith(".html") and os.path.isfile(path):
            return self._serve_html_with_snippet(path)

        return super().do_GET()

    def _serve_html_with_snippet(self, path):
        try:
            body = Path(path).read_bytes()
        except OSError:
            return super().do_GET()
        if b"</body>" in body:
            body = body.replace(b"</body>", RELOAD_SNIPPET + b"</body>", 1)
        else:
            body += RELOAD_SNIPPET
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_reload_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q: queue.Queue = queue.Queue()
        with _clients_lock:
            _clients.append(q)
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                except queue.Empty:
                    msg = b": ping\n\n"  # heartbeat detects dead sockets
                self.wfile.write(msg)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with _clients_lock:
                if q in _clients:
                    _clients.remove(q)


def _notify_all():
    with _clients_lock:
        for q in _clients:
            q.put(b"data: reload\n\n")


def _snapshot():
    snap = {}
    for p in ROOT.iterdir():
        if p.is_file() and (p.suffix in WATCH_EXT or p.name in WATCH_EXTRA):
            try:
                snap[p.name] = p.stat().st_mtime
            except OSError:
                pass
    return snap


def _watcher():
    last = _snapshot()
    while True:
        time.sleep(0.4)
        cur = _snapshot()
        if cur != last:
            changed = sorted(
                [k for k in cur if cur.get(k) != last.get(k)]
                + [k for k in last if k not in cur]
            )
            last = cur
            print(f"↻ reload ({', '.join(changed)})", flush=True)
            _notify_all()


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    threading.Thread(target=_watcher, daemon=True).start()
    srv = Server(("", PORT), Handler)
    print(f"Serving {ROOT} at http://localhost:{PORT}  (live reload ON)")
    print("Watching: *.html, *.js, *.css, tasmota.db  —  Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
