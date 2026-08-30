"""lmux daemon client — Unix domain socket JSON-RPC."""
import json
import os
import socket
import stat
import threading
import time

# Optional feature imports with graceful fallback
try:
    from agent_hooks import HookRegistry
except ImportError:
    HookRegistry = None
try:
    from focus_history import FocusHistory
except ImportError:
    FocusHistory = None


def _get_default_socket_path():
    """Construct default socket path using the current user's UID."""
    uid = os.getuid()
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    return os.path.join(xdg_runtime, "lmux.sock")


DEFAULT_SOCKET_PATH = _get_default_socket_path()
MAX_RESPONSE_SIZE = 16 * 1024 * 1024  # 16 MiB cap to prevent unbounded reads


class DaemonClient:
    """Thread-safe client for lmux daemon socket API with connection pooling (H2 fix)."""
    def __init__(self, path=None):
        self.path = path or os.environ.get(
            "LMUX_SOCKET_PATH", DEFAULT_SOCKET_PATH
        )
        self._lock = threading.Lock()
        self._sock = None  # persistent connection (H2)
        self._connected = False
        # Feature: hook registry and focus history
        self._hook_registry = HookRegistry() if HookRegistry else None
        self._focus_history = FocusHistory() if FocusHistory else None
        # Browser API bridge (set by main.py)
        self._browser_api_bridge = None

    def _verify_socket(self):
        """Verify socket exists and is owned by current user."""
        try:
            st = os.stat(self.path)
            if not stat.S_ISSOCK(st.st_mode):
                raise ConnectionRefusedError(f"{self.path} is not a socket")
            if st.st_uid != os.getuid():
                raise PermissionError(
                    f"socket {self.path} owned by uid {st.st_uid}, "
                    f"not current user {os.getuid()}"
                )
        except FileNotFoundError:
            raise

    def _connect(self):
        """Establish or re-establish persistent connection (H2 fix)."""
        if self._sock and self._connected:
            return self._sock
        self._verify_socket()
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(5)
        s.connect(self.path)
        self._sock = s
        self._connected = True
        return self._sock

    def _disconnect(self):
        """Close persistent connection."""
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
            self._connected = False

    def _recv_all(self, s):
        """Read until the daemon closes the connection or timeout.

        Caps total received bytes at MAX_RESPONSE_SIZE to prevent
        unbounded memory use from a misbehaving server.
        """
        chunks = []
        total = 0
        while True:
            try:
                chunk = s.recv(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_RESPONSE_SIZE:
                    raise OSError(
                        f"response exceeds {MAX_RESPONSE_SIZE} bytes"
                    )
                chunks.append(chunk)
                if len(chunk) < 65536:
                    break
            except socket.timeout:
                break
        return b"".join(chunks)

    def send(self, cmd, args=None):
        """Send command with persistent connection + reconnect on failure (H2 fix).
        Emits hook events and tracks focus changes after successful commands."""
        if args is None:
            args = {}

        # Handle browser commands locally via browser API bridge
        if cmd.startswith("browser.") and self._browser_api_bridge:
            return self._browser_api_bridge.handle_request({"cmd": cmd, "args": args})

        payload = json.dumps({"cmd": cmd, "args": args}) + "\n"
        last_err = None
        result = None
        for attempt in range(3):
            try:
                with self._lock:
                    s = self._connect()
                    try:
                        s.sendall(payload.encode())
                        raw = self._recv_all(s)
                    except (socket.error, OSError):
                        self._disconnect()
                        raise
                if not raw:
                    result = {"ok": False, "error": "empty response"}
                else:
                    result = json.loads(raw.decode().strip())
                break
            except PermissionError as e:
                result = {"ok": False, "error": f"permission denied: {e}"}
                break
            except (FileNotFoundError, ConnectionRefusedError, socket.error, OSError) as e:
                last_err = str(e)
                self._disconnect()
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        if result is None:
            result = {"ok": False, "error": f"connection failed after 3 attempts: {last_err}"}

        # Emit hooks and track focus after successful commands
        if result.get("ok"):
            self._post_send_hooks(cmd, args, result)

        return result

    def _post_send_hooks(self, cmd, args, result):
        """Emit hook events and track focus after a successful send."""
        try:
            if cmd == "workspace.create":
                ws = result.get("result", {})
                self._emit_hook("workspace_created",
                                workspace_id=ws.get("id", ""),
                                title=ws.get("title", ""))
            elif cmd == "workspace.close":
                self._emit_hook("workspace_closed",
                                workspace_id=args.get("id", ""))
            elif cmd == "surface.split":
                pane = result.get("result", {})
                self._emit_hook("pane_split",
                                pane_id=pane.get("id", ""),
                                direction=args.get("direction", ""))
            elif cmd == "pane.focus":
                pane_id = args.get("id", "")
                ws_id = args.get("workspace_id", "")
                self._emit_hook("pane_focused",
                                pane_id=pane_id,
                                workspace_id=ws_id)
                self.track_focus(pane_id=pane_id, workspace_id=ws_id)
            elif cmd == "surface.create":
                surf = result.get("result", {})
                self._emit_hook("surface_created",
                                surface_id=surf.get("id", ""))
            elif cmd == "surface.close":
                self._emit_hook("surface_closed",
                                surface_id=args.get("id", ""))
            elif cmd == "agent.spawn":
                agent = result.get("result", {})
                self._emit_hook("agent_spawned",
                                agent_id=agent.get("id", ""),
                                name=args.get("name", ""))
            elif cmd == "agent.stop":
                self._emit_hook("agent_stopped",
                                agent_id=args.get("id", ""))
            elif cmd == "notification.create":
                self._emit_hook("notification_received",
                                text=args.get("text", ""))
        except Exception:
            pass  # hooks must never crash the client

    def send_events(self, name_filter=None, category_filter=None, callback=None):
        """Connect to events stream and call callback for each event.
        Runs in a background thread. Returns a stop event."""
        args = {}
        if name_filter:
            args["name"] = name_filter
        if category_filter:
            args["category"] = category_filter
        stop = threading.Event()

        def _run():
            while not stop.is_set():
                try:
                    s = socket.socket(socket.AF_UNIX)
                    s.settimeout(1)
                    s.connect(self.path)
                    payload = json.dumps({"cmd": "events", "args": args}) + "\n"
                    s.sendall(payload.encode())
                    # consume until stop
                    buf = ""
                    while not stop.is_set():
                        try:
                            data = s.recv(65536)
                            if not data:
                                break
                            buf += data.decode()
                            while "\n" in buf:
                                line, buf = buf.split("\n", 1)
                                line = line.strip()
                                if line:
                                    try:
                                        evt = json.loads(line)
                                        if callback:
                                            callback(evt)
                                    except json.JSONDecodeError:
                                        pass
                        except socket.timeout:
                            continue
                    s.close()
                except Exception:
                    if not stop.is_set():
                        time.sleep(1)
                if not stop.is_set():
                    time.sleep(1)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return stop
    # ── hook & focus tracking ─────────────────────────────

    def _emit_hook(self, event, **kwargs):
        """Emit a hook event if the hook registry is available."""
        if self._hook_registry:
            try:
                self._hook_registry.emit(event, **kwargs)
            except Exception:
                pass

    def track_focus(self, pane_id=None, workspace_id=None, surface_id=None, label=None):
        """Record a focus change in the focus history."""
        if self._focus_history:
            try:
                self._focus_history.push(
                    pane_id=pane_id,
                    workspace_id=workspace_id,
                    surface_id=surface_id,
                    label=label,
                )
            except Exception:
                pass

    @property
    def hook_registry(self):
        """Access the hook registry (or None)."""
        return self._hook_registry

    @property
    def focus_history(self):
        """Access the focus history (or None)."""
        return self._focus_history
