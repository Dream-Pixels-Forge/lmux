"""SSH advanced features for lmux — session management, browser routing."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import logging
import os
import subprocess
import json
import socket
import threading
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger("lmux.ssh_advanced")


@dataclass
class SSHSession:
    """Represents an active SSH session."""
    session_id: str
    host: str
    user: str
    port: int
    control_path: str
    created_at: float
    last_active: float
    pid: Optional[int] = None
    workspace_id: Optional[str] = None
    remote_dir: Optional[str] = None
    agent_forwarding: bool = False


class SSHSessionManager:
    """Manages SSH sessions with advanced features."""

    def __init__(self, config_dir: Optional[str] = None):
        self._config_dir = Path(config_dir or "~/.config/lmux").expanduser()
        self._sessions_file = self._config_dir / "ssh_sessions.json"
        self._sessions: Dict[str, SSHSession] = {}
        self._control_dir = Path("~/.ssh/lmux-control").expanduser()
        self._control_dir.mkdir(parents=True, exist_ok=True)
        self._load_sessions()

    def _load_sessions(self):
        """Load sessions from file."""
        if self._sessions_file.exists():
            try:
                with open(self._sessions_file, "r") as f:
                    data = json.load(f)
                    for sid, session_data in data.items():
                        self._sessions[sid] = SSHSession(**session_data)
            except Exception as e:
                logger.error(f"Error loading SSH sessions: {e}")

    def _save_sessions(self):
        """Save sessions to file."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        data = {}
        for sid, session in self._sessions.items():
            data[sid] = {
                "session_id": session.session_id,
                "host": session.host,
                "user": session.user,
                "port": session.port,
                "control_path": session.control_path,
                "created_at": session.created_at,
                "last_active": session.last_active,
                "pid": session.pid,
                "workspace_id": session.workspace_id,
                "remote_dir": session.remote_dir,
                "agent_forwarding": session.agent_forwarding
            }
        with open(self._sessions_file, "w") as f:
            json.dump(data, f, indent=2)

    def _get_control_path(self, host: str, user: str, port: int) -> str:
        """Generate control path for SSH multiplexing."""
        # Use a hash to avoid long paths
        import hashlib
        target = f"{user}@{host}:{port}"
        hash_val = hashlib.md5(target.encode()).hexdigest()[:12]
        return str(self._control_dir / hash_val)

    def connect(
        self,
        host: str,
        user: str = "root",
        port: int = 22,
        key_file: Optional[str] = None,
        password: Optional[str] = None,
        remote_dir: Optional[str] = None,
        agent_forwarding: bool = False
    ) -> SSHSession:
        """Connect to a remote host with multiplexing."""
        session_id = f"{user}@{host}:{port}"
        control_path = self._get_control_path(host, user, port)

        # Build SSH command
        cmd = [
            "ssh",
            "-M",  # Master mode
            "-S", control_path,
            "-o", "ControlPersist=600",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-p", str(port)
        ]

        if key_file:
            cmd.extend(["-i", key_file])

        if agent_forwarding:
            cmd.extend(["-A"])

        cmd.extend([f"{user}@{host}", "echo", "connected"])

        try:
            # Start multiplexed connection
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
                input=password + "\n" if password else None
            )

            if result.returncode == 0:
                session = SSHSession(
                    session_id=session_id,
                    host=host,
                    user=user,
                    port=port,
                    control_path=control_path,
                    created_at=datetime.now().timestamp(),
                    last_active=datetime.now().timestamp(),
                    remote_dir=remote_dir,
                    agent_forwarding=agent_forwarding
                )
                self._sessions[session_id] = session
                self._save_sessions()
                return session
            else:
                raise ConnectionError(f"SSH connection failed: {result.stderr}")
        except subprocess.TimeoutExpired:
            raise ConnectionError("SSH connection timed out")

    def disconnect(self, session_id: str) -> bool:
        """Disconnect an SSH session."""
        if session_id not in self._sessions:
            return False

        session = self._sessions[session_id]
        control_path = session.control_path

        try:
            # Close multiplexed connection
            subprocess.run(
                ["ssh", "-S", control_path, "-O", "exit", f"{session.user}@{session.host}"],
                capture_output=True,
                timeout=5
            )
        except Exception as e:
            logger.error(f"Error closing SSH session: {e}")

        del self._sessions[session_id]
        self._save_sessions()
        return True

    def list_sessions(self) -> List[SSHSession]:
        """List all active SSH sessions."""
        # Clean up dead sessions
        self._cleanup_dead()
        return list(self._sessions.values())

    def get_session(self, session_id: str) -> Optional[SSHSession]:
        """Get a specific session."""
        return self._sessions.get(session_id)

    def attach(self, session_id: str) -> Optional[str]:
        """Get the attach command for a session."""
        if session_id not in self._sessions:
            return None

        session = self._sessions[session_id]
        return f"ssh -S {session.control_path} {session.user}@{session.host}"

    def pty_attach(self, session_id: str) -> bool:
        """Attach to session with PTY."""
        if session_id not in self._sessions:
            return False

        session = self._sessions[session_id]
        try:
            # Open a new PTY connection through the multiplexer
            cmd = [
                "ssh",
                "-S", session.control_path,
                "-t",  # Force PTY
                f"{session.user}@{session.host}"
            ]
            subprocess.Popen(cmd)
            return True
        except Exception as e:
            logger.error(f"Error attaching to SSH session: {e}")
            return False

    def cleanup(self):
        """Clean up all sessions."""
        for session_id in list(self._sessions.keys()):
            self.disconnect(session_id)

    def _cleanup_dead(self):
        """Clean up dead sessions."""
        dead = []
        for sid, session in self._sessions.items():
            try:
                # Check if control socket exists
                if not Path(session.control_path).exists():
                    dead.append(sid)
            except Exception:
                dead.append(sid)

        for sid in dead:
            del self._sessions[sid]

        if dead:
            self._save_sessions()


class SSHBrowserRouter:
    """Routes browser traffic through SSH tunnels."""

    def __init__(self, session_manager: SSHSessionManager):
        self._session_manager = session_manager
        self._tunnels: Dict[str, Dict[str, Any]] = {}

    def create_tunnel(
        self,
        session_id: str,
        local_port: int,
        remote_host: str = "localhost",
        remote_port: int = 8080
    ) -> Optional[int]:
        """Create an SSH tunnel for browser routing."""
        session = self._session_manager.get_session(session_id)
        if not session:
            return None

        try:
            # Find available local port if requested port is 0
            if local_port == 0:
                local_port = self._find_available_port()

            # Create tunnel
            cmd = [
                "ssh",
                "-S", session.control_path,
                "-L", f"{local_port}:{remote_host}:{remote_port}",
                "-f",  # Background
                "-N",  # No command
                f"{session.user}@{session.host}"
            ]

            result = subprocess.run(cmd, capture_output=True, timeout=5)

            if result.returncode == 0:
                tunnel_id = f"{session_id}:{local_port}"
                self._tunnels[tunnel_id] = {
                    "session_id": session_id,
                    "local_port": local_port,
                    "remote_host": remote_host,
                    "remote_port": remote_port
                }
                return local_port
            else:
                logger.error(f"Failed to create SSH tunnel: {result.stderr}")
                return None
        except Exception as e:
            logger.error(f"Error creating SSH tunnel: {e}")
            return None

    def close_tunnel(self, tunnel_id: str) -> bool:
        """Close an SSH tunnel."""
        if tunnel_id not in self._tunnels:
            return False

        tunnel = self._tunnels[tunnel_id]
        session = self._session_manager.get_session(tunnel["session_id"])

        if session:
            try:
                # Find and kill the tunnel process
                cmd = [
                    "ssh",
                    "-S", session.control_path,
                    "-O", "cancel",
                    "-L", f"{tunnel['local_port']}:{tunnel['remote_host']}:{tunnel['remote_port']}",
                    f"{session.user}@{session.host}"
                ]
                subprocess.run(cmd, capture_output=True, timeout=5)
            except Exception as e:
                logger.error(f"Error closing SSH tunnel: {e}")

        del self._tunnels[tunnel_id]
        return True

    def list_tunnels(self) -> List[Dict[str, Any]]:
        """List all active tunnels."""
        return list(self._tunnels.values())

    def get_browser_url(self, tunnel_id: str, path: str = "") -> Optional[str]:
        """Get browser URL through tunnel."""
        if tunnel_id not in self._tunnels:
            return None

        tunnel = self._tunnels[tunnel_id]
        return f"http://localhost:{tunnel['local_port']}/{path}"

    def _find_available_port(self) -> int:
        """Find an available local port."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]


class SSHDragUpload(Gtk.TargetEntry):
    """Target entry for drag-and-drop file upload to SSH."""

    def __init__(self):
        super().__init__("text/uri-list", 0, 0)


class SSHDragDropHandler:
    """Handles drag-and-drop file uploads to SSH sessions."""

    def __init__(self, session_manager: SSHSessionManager):
        self._session_manager = session_manager

    def handle_drop(
        self,
        uris: List[str],
        session_id: str,
        remote_dir: str = "~"
    ) -> List[Dict[str, Any]]:
        """Handle dropped files and upload to SSH session."""
        results = []
        session = self._session_manager.get_session(session_id)

        if not session:
            return [{"success": False, "error": "Session not found"}]

        for uri in uris:
            # Convert URI to local path
            if uri.startswith("file://"):
                local_path = uri[7:]
            else:
                local_path = uri

            # Upload via scp
            try:
                cmd = [
                    "scp",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "UserKnownHostsFile=/dev/null",
                    local_path,
                    f"{session.user}@{session.host}:{remote_dir}/"
                ]

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

                results.append({
                    "success": result.returncode == 0,
                    "local_path": local_path,
                    "remote_path": f"{remote_dir}/{os.path.basename(local_path)}",
                    "error": result.stderr if result.returncode != 0 else None
                })
            except Exception as e:
                results.append({
                    "success": False,
                    "local_path": local_path,
                    "error": str(e)
                })

        return results


def create_ssh_session_panel(manager: SSHSessionManager) -> Gtk.Box:
    """Create a panel showing SSH sessions."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

    # Header
    header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    title = Gtk.Label(label="SSH Sessions")
    title.set_xalign(0)
    title.get_style_context().add_class("heading")
    header.pack_start(title, True, True, 0)

    refresh_btn = Gtk.Button(label="Refresh")
    header.pack_end(refresh_btn, False, False, 0)
    box.pack_start(header, False, False, 0)

    # Session list
    listbox = Gtk.ListBox()
    listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)

    def refresh_sessions():
        for child in listbox.get_children():
            listbox.remove(child)

        sessions = manager.list_sessions()
        for session in sessions:
            row = Gtk.ListBoxRow()
            row._session = session

            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            hbox.set_margin_start(8)
            hbox.set_margin_end(8)
            hbox.set_margin_top(4)
            hbox.set_margin_bottom(4)

            # Status indicator
            status = Gtk.Label(label="●")
            status.get_style_context().add_class("connected-indicator")
            hbox.pack_start(status, False, False, 0)

            # Session info
            info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            host_label = Gtk.Label(label=f"{session.user}@{session.host}:{session.port}")
            host_label.set_xalign(0)
            info.pack_start(host_label, False, False, 0)

            meta = Gtk.Label(label=f"Control: {session.control_path}")
            meta.set_xalign(0)
            meta.get_style_context().add_class("dim-label")
            info.pack_start(meta, False, False, 0)

            hbox.pack_start(info, True, True, 0)

            # Actions
            attach_btn = Gtk.Button(label="Attach")
            attach_btn.connect("clicked", lambda _, s=session: manager.pty_attach(s.session_id))
            hbox.pack_end(attach_btn, False, False, 0)

            detach_btn = Gtk.Button(label="Disconnect")
            detach_btn.connect("clicked", lambda _, s=session: (
                manager.disconnect(s.session_id),
                refresh_sessions()
            ))
            hbox.pack_end(detach_btn, False, False, 0)

            row.add(hbox)
            listbox.add(row)

        listbox.show_all()

    refresh_btn.connect("clicked", lambda _: refresh_sessions())
    refresh_sessions()

    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.add(listbox)
    box.pack_start(scroll, True, True, 0)

    return box
