"""lmux web dashboard — lightweight HTTP API + static file server.

Usage:
    python3 web/server.py [--port 8080] [--host 127.0.0.1]

Endpoints:
    GET  /api/tree          — workspace/surface/pane tree
    GET  /api/workspaces    — workspace list
    GET  /api/events        — SSE event stream
    POST /api/command       — execute daemon command
    GET  /api/agents        — list agents
    GET  /api/notifications — list notifications
    GET  /                  — dashboard UI
"""
import http.server
import json
import os
import sys
import socketserver
import threading
import time
import urllib.parse
import urllib.request

# Add parent dir for daemon_client import
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gui"))
from daemon_client import DaemonClient


# ── Configuration ───────────────────────────────────────────

DEFAULT_PORT = 8080
DEFAULT_HOST = "127.0.0.1"
WEB_DIR = os.path.dirname(os.path.abspath(__file__))


# ── API handler ─────────────────────────────────────────────

class LmuxAPIHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler that serves API endpoints and static files."""

    client = None  # Set by main()
    eventslients = []  # SSE client connections

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/tree":
            self._json_response(self._api_tree())
        elif path == "/api/workspaces":
            self._json_response(self._api_workspaces())
        elif path == "/api/events":
            self._handle_sse()
        elif path == "/api/agents":
            self._json_response(self._api_agents())
        elif path == "/api/notifications":
            self._json_response(self._api_notifications())
        elif path == "/api/ping":
            self._json_response({"ok": True, "version": "1.0.0"})
        elif path == "/" or path == "/index.html":
            self._serve_file("index.html", "text/html")
        elif path.endswith(".css"):
            self._serve_file(path.lstrip("/"), "text/css")
        elif path.endswith(".js"):
            self._serve_file(path.lstrip("/"), "application/javascript")
        elif path.endswith(".svg"):
            self._serve_file(path.lstrip("/"), "image/svg+xml")
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/command":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body)
                command = data.get("command", "")
                args = data.get("args", {})
                result = self.client.send(command, args)
                self._json_response(result)
            except json.JSONDecodeError:
                self._json_response({"ok": False, "error": "Invalid JSON"}, 400)
            except Exception as e:
                self._json_response({"ok": False, "error": str(e)}, 500)
        else:
            self.send_error(404)

    def _json_response(self, data, status=200):
        """Send JSON response with CORS headers."""
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, filename, content_type):
        """Serve a static file from the web directory."""
        filepath = os.path.join(WEB_DIR, filename)
        if not os.path.isfile(filepath):
            self.send_error(404)
            return
        with open(filepath, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_sse(self):
        """Handle Server-Sent Events stream."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        # Send initial keepalive
        self.wfile.write(b": keepalive\n\n")
        self.wfile.flush()

        # Subscribe to daemon events
        stop_event = threading.Event()

        def on_event(evt):
            try:
                data = json.dumps(evt)
                self.wfile.write(f"data: {data}\n\n".encode())
                self.wfile.flush()
            except Exception:
                stop_event.set()

        stop = self.client.send_events(callback=on_event)

        # Keep connection alive until client disconnects
        try:
            while not stop_event.is_set():
                time.sleep(1)
                try:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                except Exception:
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            if stop:
                stop.set()

    def _api_tree(self):
        """Get workspace tree."""
        return self.client.send("tree")

    def _api_workspaces(self):
        """Get workspace list."""
        return self.client.send("workspace.list")

    def _api_agents(self):
        """Get agent list."""
        return self.client.send("agent.list")

    def _api_notifications(self):
        """Get notification list."""
        return self.client.send("notification.list")

    def log_message(self, format, *args):
        """Suppress default HTTP logging."""
        pass


# ── Threaded server ─────────────────────────────────────────

class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """HTTP server that handles each request in a new thread."""
    daemon_threads = True
    allow_reuse_address = True


# ── Main ────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="lmux web dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default=DEFAULT_HOST)
    args = parser.parse_args()

    client = DaemonClient()
    LmuxAPIHandler.client = client

    server = ThreadedHTTPServer((args.host, args.port), LmuxAPIHandler)
    print(f"lmux web dashboard running at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
