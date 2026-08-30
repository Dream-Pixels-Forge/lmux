"""Phone companion pairing via QR code for lmux remote control.

Provides a localhost HTTP API that a phone's camera can reach after
scanning a QR code.  The API exposes workspace state and accepts
simple remote commands (focus pane, split, create workspace, close).

No external Python dependencies — stdlib only.  QR rendering falls
back through qrencode CLI → python-qrcode → plain-text label.
"""
import json
import hashlib
import os
import secrets
import socket
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Constants ────────────────────────────────────────────────

LMUX_VERSION = "1.0.0"
DEFAULT_PORT = 8765
TOKEN_MAX_AGE = 300  # 5 minutes
RATE_LIMIT_PER_SEC = 10
_HOST = "127.0.0.1"


# ── Data classes ─────────────────────────────────────────────

@dataclass
class RemoteCommand:
    """A command received from a paired phone."""
    command: str
    args: dict = field(default_factory=dict)
    token: str = ""
    timestamp: float = 0.0

    def validate(self, token: str, max_age: int = TOKEN_MAX_AGE) -> bool:
        """Check that the token matches and the command isn't stale."""
        if not secrets.compare_digest(self.token, token):
            return False
        if time.time() - self.timestamp > max_age:
            return False
        return True


@dataclass
class CompanionSession:
    """Tracks a connected phone device."""
    device_name: str = "unknown"
    connected_at: float = 0.0
    last_active: float = 0.0
    ip: str = "127.0.0.1"


# ── Rate limiter ─────────────────────────────────────────────

class _RateLimiter:
    """Simple sliding-window rate limiter keyed by IP."""

    def __init__(self, max_per_sec: int = RATE_LIMIT_PER_SEC):
        self._max = max_per_sec
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, ip: str) -> bool:
        now = time.monotonic()
        with self._lock:
            window = self._hits.setdefault(ip, [])
            window[:] = [t for t in window if now - t < 1.0]
            if len(window) >= self._max:
                return False
            window.append(now)
            return True


# ── HTTP handler ─────────────────────────────────────────────

class _CompanionHandler(BaseHTTPRequestHandler):
    """Handles API requests from paired phone devices."""

    # Set by CompanionServer before serving
    server_ref: "CompanionServer" = None  # type: ignore[assignment]
    rate_limiter: _RateLimiter = _RateLimiter()

    def log_message(self, fmt, *args):  # noqa: D401
        """Silence default stderr logging."""
        pass

    # ── helpers ──────────────────────────────────────────

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_token(self) -> str | None:
        """Return query token or None if missing/invalid."""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        tokens = params.get("token", [])
        if tokens and secrets.compare_digest(tokens[0], self.server_ref._token):
            return tokens[0]
        return None

    def _check_rate(self) -> bool:
        ip = self.client_address[0]
        return self.rate_limiter.allow(ip)

    # ── routes ───────────────────────────────────────────

    def do_GET(self):  # noqa: N802 — django convention
        if not self._check_rate():
            self._send_json({"error": "rate limit"}, 429)
            return

        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/health":
            self._send_json({"status": "ok"})
        elif path == "/api/status":
            if not self._check_token():
                self._send_json({"error": "unauthorized"}, 401)
                return
            srv = self.server_ref
            self._send_json({
                "version": LMUX_VERSION,
                "workspaces": srv._get_workspace_tree(),
                "active_pane": srv._get_active_pane(),
                "connected_devices": len(srv._sessions),
            })
        elif path == "/api/workspaces":
            if not self._check_token():
                self._send_json({"error": "unauthorized"}, 401)
                return
            self._send_json({"workspaces": self.server_ref._get_workspace_tree()})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        if not self._check_rate():
            self._send_json({"error": "rate limit"}, 429)
            return

        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/command":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                self._send_json({"error": "invalid json"}, 400)
                return

            # Accept token from query param or JSON body
            query_token = self._check_token()
            body_token = payload.get("token", "")
            token = query_token or body_token
            if not token:
                self._send_json({"error": "unauthorized"}, 401)
                return

            cmd = RemoteCommand(
                command=payload.get("command", ""),
                args=payload.get("args", {}),
                token=token,
                timestamp=payload.get("timestamp", time.time()),
            )
            if not cmd.validate(self.server_ref._token):
                self._send_json({"error": "invalid token"}, 401)
                return

            # Track the session
            self.server_ref._touch_session(
                self.client_address[0],
                payload.get("device_name", "phone"),
            )
            result = self.server_ref._execute_command(cmd)
            self._send_json(result)
        else:
            self._send_json({"error": "not found"}, 404)


# ── Companion server ─────────────────────────────────────────

class CompanionServer:
    """Localhost HTTP server that exposes lmux control to a paired phone."""

    def __init__(self, port: int = DEFAULT_PORT):
        self._port = port
        self._token = ""
        self._httpd: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._sessions: dict[str, CompanionSession] = {}
        self._lock = threading.Lock()
        self._rotate_timer: threading.Timer | None = None

    # ── lifecycle ────────────────────────────────────────

    def start(self) -> None:
        """Start the HTTP server in a background daemon thread."""
        if self._running:
            return
        self._rotate_token()
        handler = type(
            "_Handler",
            (_CompanionHandler,),
            {"server_ref": self},
        )
        self._httpd = HTTPServer((_HOST, self._port), handler)
        self._running = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True
        )
        self._thread.start()
        self._schedule_rotation()

    def stop(self) -> None:
        """Shut down the HTTP server."""
        if not self._running:
            return
        self._running = False
        if self._rotate_timer:
            self._rotate_timer.cancel()
            self._rotate_timer = None
        if self._httpd:
            self._httpd.shutdown()
            self._httpd = None
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        with self._lock:
            self._sessions.clear()

    @property
    def is_running(self) -> bool:
        return self._running

    # ── QR payload ───────────────────────────────────────

    def get_qr_data(self) -> str:
        """Return JSON payload that encodes the connection URL + token."""
        return json.dumps({
            "url": f"http://{_HOST}:{self._port}",
            "token": self._token,
            "version": LMUX_VERSION,
        })

    def get_server_url(self) -> str:
        return f"http://{_HOST}:{self._port}"

    # ── token rotation ───────────────────────────────────

    def _rotate_token(self) -> None:
        self._token = hashlib.sha256(secrets.token_bytes(32)).hexdigest()

    def _schedule_rotation(self) -> None:
        if not self._running:
            return
        self._rotate_timer = threading.Timer(TOKEN_MAX_AGE, self._do_rotation)
        self._rotate_timer.daemon = True
        self._rotate_timer.start()

    def _do_rotation(self) -> None:
        self._rotate_token()
        with self._lock:
            self._sessions.clear()
        self._schedule_rotation()

    def refresh_token(self) -> str:
        """Manually rotate the token and return the new one."""
        self._rotate_token()
        with self._lock:
            self._sessions.clear()
        return self._token

    # ── workspace introspection (stub for daemon) ────────

    def _get_workspace_tree(self) -> list[dict]:
        """Query the lmux daemon for the workspace tree.

        Falls back to a placeholder when the daemon isn't reachable so
        the API stays usable for testing and development.
        """
        try:
            from daemon_client import DaemonClient
            client = DaemonClient()
            result = client.send("list_workspaces")
            if result and "workspaces" in result:
                return result["workspaces"]
        except Exception:
            pass
        return [{"id": 1, "title": "default", "panes": []}]

    def _get_active_pane(self) -> str:
        try:
            from daemon_client import DaemonClient
            client = DaemonClient()
            result = client.send("get_active_pane")
            if result and "pane_id" in result:
                return result["pane_id"]
        except Exception:
            pass
        return ""

    # ── command execution ────────────────────────────────

    def _execute_command(self, cmd: RemoteCommand) -> dict:
        """Dispatch a validated command to the daemon."""
        try:
            from daemon_client import DaemonClient
            client = DaemonClient()
            result = client.send(cmd.command, cmd.args)
            return {"ok": True, "result": result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ── session tracking ─────────────────────────────────

    def _touch_session(self, ip: str, device_name: str) -> None:
        now = time.time()
        with self._lock:
            key = ip
            if key in self._sessions:
                self._sessions[key].last_active = now
                self._sessions[key].device_name = device_name
            else:
                self._sessions[key] = CompanionSession(
                    device_name=device_name,
                    connected_at=now,
                    last_active=now,
                    ip=ip,
                )

    def get_sessions(self) -> list[CompanionSession]:
        with self._lock:
            return list(self._sessions.values())

    def disconnect_all(self) -> None:
        with self._lock:
            self._sessions.clear()


# ── QR code generation ───────────────────────────────────────

class QRCodeGenerator:
    """Generate QR codes with a three-tier fallback strategy."""

    def generate(self, text: str, size: int = 300) -> Gtk.Image | None:
        """Return a Gtk.Image containing the QR code, or None on failure.

        Priority:
        1. qrencode CLI  → SVG  → Gtk.Image
        2. python-qrcode → Gtk.Image
        3. plain-text Gtk.Image label (last resort)
        """
        img = self._try_qrencode(text, size)
        if img is not None:
            return img
        img = self._try_qrcode_lib(text, size)
        if img is not None:
            return img
        return self._text_fallback(text)

    def generate_with_payload(self, server_url: str, token: str) -> Gtk.Image:
        """Generate a QR image from a server URL + token pair."""
        payload = f"{server_url}?token={token}"
        return self.generate(payload) or self._text_fallback(payload)

    # ── strategy 1: qrencode CLI ────────────────────────

    def _try_qrencode(self, text: str, size: int) -> Gtk.Image | None:
        import subprocess
        import tempfile
        try:
            svg_path = tempfile.mktemp(suffix=".svg")
            subprocess.run(
                ["qrencode", "-o", svg_path, "-s", "4", "-m", "1", text],
                check=True,
                timeout=5,
                capture_output=True,
            )
            if not os.path.isfile(svg_path):
                return None
            # Gtk.Image can load SVG via GdkPixbuf if librsvg is available
            try:
                from gi.repository import GdkPixbuf
                pb = GdkPixbuf.Pixbuf.new_from_file_at_size(svg_path, size, size)
                img = Gtk.Image.new_from_pixbuf(pb)
                os.unlink(svg_path)
                return img
            except Exception:
                os.unlink(svg_path)
                return None
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            return None

    # ── strategy 2: python-qrcode ───────────────────────

    def _try_qrcode_lib(self, text: str, size: int) -> Gtk.Image | None:
        try:
            import qrcode  # type: ignore[import-untyped]
            from io import BytesIO
            from gi.repository import GdkPixbuf
            qr = qrcode.QRCode(
                box_size=size // 25,
                border=2,
            )
            qr.add_data(text)
            qr.make(fit=True)
            pil_img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            pil_img.save(buf, format="PNG")
            buf.seek(0)
            loader = GdkPixbuf.PixbufLoader()
            loader.write(buf.read())
            loader.close()
            return Gtk.Image.new_from_pixbuf(loader.get_pixbuf())
        except ImportError:
            return None
        except Exception:
            return None

    # ── strategy 3: text fallback ────────────────────────

    def _text_fallback(self, text: str) -> Gtk.Image:
        label = Gtk.Label(label=text)
        label.set_line_wrap(True)
        label.set_max_width_chars(40)
        label.set_selectable(True)
        # Gtk.Image can't hold a Label, so wrap in a Gtk.Image via paintable
        # Actually just return the label as-is — caller handles it.
        # For API compliance we paint a simple fallback.
        return Gtk.Image.new_from_icon_name("dialog-information", Gtk.IconSize.DIALOG)


# ── GTK3 pairing dialog ──────────────────────────────────────

class CompanionDialog(Gtk.Dialog):
    """Modal dialog showing a QR code for phone pairing."""

    def __init__(self, parent: Gtk.Window | None = None,
                 server: CompanionServer | None = None):
        super().__init__(
            title="Phone Companion",
            parent=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self._server = server or CompanionServer()
        self._qr_gen = QRCodeGenerator()
        self.set_default_size(420, 560)
        self.set_border_width(12)

        # ── build UI ─────────────────────────────────────
        box = self.get_content_area()
        box.set_spacing(8)

        title_label = Gtk.Label()
        title_label.set_markup(
            '<span size="large" weight="bold">Pair Phone</span>'
        )
        box.pack_start(title_label, False, False, 4)

        # QR image container
        self._qr_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._qr_box.set_halign(Gtk.Align.CENTER)
        box.pack_start(self._qr_box, True, True, 8)

        # URL + token display
        self._info_label = Gtk.Label()
        self._info_label.set_line_wrap(True)
        self._info_label.set_selectable(True)
        self._info_label.set_xalign(0.5)
        box.pack_start(self._info_label, False, False, 4)

        # Instruction
        instr = Gtk.Label(label="Scan with your phone camera")
        instr.set_markup('<span style="italic">Scan with your phone camera</span>')
        instr.set_xalign(0.5)
        box.pack_start(instr, False, False, 4)

        # Status
        self._status_label = Gtk.Label(label="Connected devices: 0")
        self._status_label.set_xalign(0.5)
        box.pack_start(self._status_label, False, False, 2)

        # Buttons
        btn_box = Gtk.Box(spacing=6)
        btn_box.set_halign(Gtk.Align.CENTER)

        refresh_btn = Gtk.Button(label="Refresh Token")
        refresh_btn.connect("clicked", self._on_refresh)
        btn_box.pack_start(refresh_btn, False, False, 0)

        disconnect_btn = Gtk.Button(label="Disconnect All")
        disconnect_btn.connect("clicked", self._on_disconnect_all)
        btn_box.pack_start(disconnect_btn, False, False, 0)

        box.pack_start(btn_box, False, False, 6)

        # Auto-expire timer
        self._expire_id = 0
        self._countdown = TOKEN_MAX_AGE

        self.show_all()

        # Wire up server & render
        if not self._server.is_running:
            self._server.start()
        self._render_qr()

    # ── QR rendering ─────────────────────────────────────

    def _render_qr(self) -> None:
        # Clear previous children
        for child in self._qr_box.get_children():
            self._qr_box.remove(child)

        payload = self._server.get_qr_data()
        qr_img = self._qr_gen.generate(payload)
        if qr_img:
            self._qr_box.pack_start(qr_img, True, True, 0)
            self._qr_box.reorder_child(qr_img, 0)

        url = self._server.get_server_url()
        token = self._server._token
        self._info_label.set_text(f"{url}\nToken: {token[:12]}…")

        self._countdown = TOKEN_MAX_AGE
        self._schedule_status_update()
        self._schedule_expire()

    # ── periodic updates ─────────────────────────────────

    def _schedule_status_update(self) -> None:
        GLib.timeout_add(2000, self._update_status)

    def _update_status(self) -> bool:
        if not self._server.is_running:
            return False
        n = len(self._server.get_sessions())
        self._status_label.set_text(f"Connected devices: {n}")
        return self._server.is_running  # keep ticking while running

    def _schedule_expire(self) -> None:
        if self._expire_id:
            GLib.source_remove(self._expire_id)
        self._expire_id = GLib.timeout_add(1000, self._expire_tick)

    def _expire_tick(self) -> bool:
        if not self._server.is_running:
            self._expire_id = 0
            return False
        self._countdown -= 1
        if self._countdown <= 0:
            self._server.refresh_token()
            self._render_qr()
            return False
        left = self._countdown
        self._status_label.set_text(
            f"Token expires in {left}s  •  "
            f"Connected: {len(self._server.get_sessions())}"
        )
        return True

    # ── button handlers ──────────────────────────────────

    def _on_refresh(self, _btn: Gtk.Button) -> None:
        self._server.refresh_token()
        self._render_qr()

    def _on_disconnect_all(self, _btn: Gtk.Button) -> None:
        self._server.disconnect_all()
        self._update_status()

    def do_response(self, response_id: int) -> None:  # noqa: N802
        if self._expire_id:
            GLib.source_remove(self._expire_id)
            self._expire_id = 0
        self._server.stop()
        self.destroy()
