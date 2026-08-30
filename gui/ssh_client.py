"""SSH remote session client for lmux.

Uses subprocess (ssh/scp) with ControlMaster multiplexing for connection reuse.
No paramiko dependency — relies on the system OpenSSH client.
"""
import atexit
import os
import signal
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# ── constants ────────────────────────────────────────────────
_SOCKET_DIR = Path.home() / ".lmux" / "ssh" / "sockets"
_SOCKET_DIR.mkdir(parents=True, exist_ok=True)
_SSH_BIN = shutil.which("ssh") or "ssh"
_SCP_BIN = shutil.which("scp") or "scp"
_SSHPASS_BIN = shutil.which("sshpass")  # may be None


@dataclass
class SSHHost:
    """Descriptor for a remote SSH host."""
    host: str
    port: int = 22
    user: str = ""
    key_file: str = ""
    agent_forwarding: bool = False

    @property
    def ssh_uri(self) -> str:
        """Return user@host or just host."""
        user_part = f"{self.user}@" if self.user else ""
        return f"{user_part}{self.host}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SSHHost":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class SSHClient:
    """SSH client that multiplexes connections via ControlMaster.

    Each logical session gets its own control socket under ~/.lmux/ssh/sockets/.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: Dict[str, dict] = {}  # session_id -> metadata

    # ── socket path helpers ──────────────────────────────────

    def _socket_path(self, session_id: str) -> str:
        """Return the ControlPath socket for a session."""
        return str(_SOCKET_DIR / f"lmux-{session_id}.sock")

    def _cleanup_socket(self, session_id: str) -> None:
        """Remove a stale control socket file."""
        sp = Path(self._socket_path(session_id))
        if sp.exists():
            try:
                sp.unlink()
            except OSError:
                pass

    # ── session lifecycle ────────────────────────────────────

    def connect(self, host: SSHHost, password: str = "",
                remote_dir: str = "") -> str:
        """Open a multiplexed SSH connection. Returns session_id.

        Raises ConnectionError if the connection cannot be established.
        """
        session_id = uuid.uuid4().hex[:12]
        sp = self._socket_path(session_id)

        cmd = self._build_base_cmd(host, password, multiplex=True,
                                    socket_path=sp)
        if remote_dir:
            cmd += ["-t", f"cd {remote_dir} && exec $SHELL"]
        else:
            cmd += ["-t"]

        # Start ControlMaster in background
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise ConnectionError(
                f"SSH binary not found: {exc}"
            ) from exc

        # Give ControlMaster a moment to establish the socket
        time.sleep(0.6)

        # Verify socket appeared
        if not Path(sp).exists():
            stderr_out = b""
            try:
                stderr_out = proc.communicate(timeout=3)[1]
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            msg = stderr_out.decode(errors="replace").strip() or "unknown error"
            raise ConnectionError(
                f"SSH ControlMaster failed for {host.ssh_uri}: {msg}"
            )

        with self._lock:
            self._sessions[session_id] = {
                "host": host.to_dict(),
                "pid": proc.pid,
                "socket": sp,
                "created": time.time(),
                "remote_dir": remote_dir,
            }

        return session_id

    def disconnect(self, session_id: str) -> bool:
        """Close a multiplexed session. Returns True if it was active."""
        with self._lock:
            info = self._sessions.pop(session_id, None)
        if info is None:
            return False

        sp = info["socket"]
        # Tell ControlMaster to exit gracefully
        try:
            subprocess.run(
                [_SSH_BIN, "-O", "exit",
                 "-o", f"ControlPath={sp}", "exit"],
                timeout=5,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        self._cleanup_socket(session_id)
        return True

    def is_alive(self, session_id: str) -> bool:
        """Check whether a session's ControlMaster is still running."""
        with self._lock:
            info = self._sessions.get(session_id)
        if info is None:
            return False
        # Check socket exists and the process is alive
        if not Path(info["socket"]).exists():
            return False
        try:
            os.kill(info["pid"], 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def exec(self, session_id: str, cmd: str, timeout: int = 30) -> str:
        """Execute a command over an active session. Returns stdout+stderr.

        Raises ConnectionError if the session is dead.
        Raises TimeoutError if the command exceeds *timeout* seconds.
        """
        with self._lock:
            info = self._sessions.get(session_id)
        if info is None:
            raise ConnectionError(f"Unknown session: {session_id}")

        sp = info["socket"]
        host_info = SSHHost.from_dict(info["host"])

        run_cmd = [
            _SSH_BIN,
            "-o", f"ControlPath={sp}",
            "-o", "ControlMaster=no",
            str(host_info.ssh_uri),
            cmd,
        ]

        try:
            result = subprocess.run(
                run_cmd, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise TimeoutError(
                f"Command timed out after {timeout}s: {cmd}"
            )

        output = result.stdout
        if result.returncode != 0 and result.stderr:
            output += f"\n[stderr] {result.stderr}"
        return output

    def scp_upload(self, session_id: str, local_path: str,
                   remote_path: str) -> bool:
        """Upload a file via SCP over an active session."""
        with self._lock:
            info = self._sessions.get(session_id)
        if info is None:
            raise ConnectionError(f"Unknown session: {session_id}")

        sp = info["socket"]
        host_info = SSHHost.from_dict(info["host"])

        cmd = [
            _SCP_BIN,
            "-o", f"ControlPath={sp}",
            "-o", "ControlMaster=no",
            "-P", str(host_info.port),
            local_path,
            f"{host_info.ssh_uri}:{remote_path}",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def scp_download(self, session_id: str, remote_path: str,
                     local_path: str) -> bool:
        """Download a file via SCP over an active session."""
        with self._lock:
            info = self._sessions.get(session_id)
        if info is None:
            raise ConnectionError(f"Unknown session: {session_id}")

        sp = info["socket"]
        host_info = SSHHost.from_dict(info["host"])

        cmd = [
            _SCP_BIN,
            "-o", f"ControlPath={sp}",
            "-o", "ControlMaster=no",
            "-P", str(host_info.port),
            f"{host_info.ssh_uri}:{remote_path}",
            local_path,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    # ── session list / cleanup ───────────────────────────────

    def list_sessions(self) -> List[dict]:
        """Return a list of active session metadata dicts."""
        with self._lock:
            return [
                {"session_id": sid, **info}
                for sid, info in self._sessions.items()
            ]

    def cleanup_all(self) -> int:
        """Disconnect all sessions. Returns count of sessions closed."""
        with self._lock:
            ids = list(self._sessions.keys())
        count = 0
        for sid in ids:
            if self.disconnect(sid):
                count += 1
        return count

    # ── internals ────────────────────────────────────────────

    def _build_base_cmd(self, host: SSHHost, password: str = "",
                        multiplex: bool = True,
                        socket_path: str = "") -> list:
        """Construct the ssh command with ControlMaster options."""
        cmd = [_SSH_BIN]

        # ControlMaster / ControlPath for multiplexing
        if multiplex and socket_path:
            cmd += [
                "-o", "ControlMaster=yes",
                "-o", "ControlPath=" + socket_path,
                "-o", "ControlPersist=no",
            ]

        # Port
        if host.port != 22:
            cmd += ["-p", str(host.port)]

        # Key file
        if host.key_file:
            cmd += ["-i", host.key_file]

        # Agent forwarding
        if host.agent_forwarding:
            cmd += ["-A"]

        # Batch mode (non-interactive) for password-less auth
        if not password:
            cmd += ["-o", "BatchMode=yes"]

        # Auto-accept new host keys
        cmd += ["-o", "StrictHostKeyChecking=accept-new"]

        # Password auth via sshpass if available
        if password and _SSHPASS_BIN:
            cmd = [_SSHPASS_BIN, "-p", password] + cmd

        cmd.append(host.ssh_uri)
        return cmd


# ── session manager singleton ────────────────────────────────

class SSHSessionManager:
    """Singleton manager that tracks all SSH sessions and cleans up on exit.

    Register cleanup via ``register_cleanup()`` on first use; this hooks
    ``atexit`` and ``SIGTERM``/``SIGINT`` to disconnect every session.
    """

    _instance: Optional["SSHSessionManager"] = None
    _init_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._client = SSHClient()
        self._registered = False

    @property
    def client(self) -> SSHClient:
        return self._client

    def register_cleanup(self) -> None:
        """Register atexit + signal handlers for cleanup. Call once."""
        if self._registered:
            return
        self._registered = True
        atexit.register(self._cleanup)
        for sig in (signal.SIGTERM, signal.SIGINT):
            prev = signal.getsignal(sig)

            def _handler(signum, frame, _prev=prev):
                self._cleanup()
                if callable(_prev):
                    _prev(signum, frame)
                elif _prev == signal.SIG_DFL:
                    signal.signal(signum, signal.SIG_DFL)
                    os.kill(os.getpid(), signum)

            signal.signal(sig, _handler)

    def _cleanup(self) -> None:
        """Close all SSH sessions."""
        self._client.cleanup_all()


# ── module-level convenience ─────────────────────────────────

_manager = SSHSessionManager()


def get_ssh_client() -> SSHClient:
    """Return the shared SSHClient, registering cleanup on first use."""
    _manager.register_cleanup()
    return _manager.client


def quick_connect(host: str, port: int = 22, user: str = "",
                  key_file: str = "", password: str = "",
                  remote_dir: str = "",
                  agent_forwarding: bool = False) -> str:
    """Convenience: connect and return session_id.

    Raises ConnectionError on failure.
    """
    h = SSHHost(
        host=host, port=port, user=user, key_file=key_file,
        agent_forwarding=agent_forwarding,
    )
    return get_ssh_client().connect(h, password=password,
                                     remote_dir=remote_dir)
