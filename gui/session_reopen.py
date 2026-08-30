"""Reopen previous session for lmux."""
import json
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger("lmux.session_reopen")


@dataclass
class SessionState:
    """State of a session for reopening."""
    session_id: str
    name: str
    timestamp: float
    workspaces: List[Dict[str, Any]] = field(default_factory=list)
    browser_url: Optional[str] = None
    window_geometry: Optional[Dict[str, int]] = None
    agent_sessions: List[Dict[str, Any]] = field(default_factory=list)


class SessionReopenManager:
    """Manages session state for reopening."""

    def __init__(self, config_dir: Optional[str] = None):
        self._config_dir = Path(config_dir or "~/.config/lmux").expanduser()
        self._sessions_dir = self._config_dir / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._current_session: Optional[SessionState] = None

    def save_session(self, state: SessionState):
        """Save session state."""
        state.timestamp = datetime.now().timestamp()
        self._current_session = state

        session_file = self._sessions_dir / f"{state.session_id}.json"
        try:
            with open(session_file, "w") as f:
                json.dump({
                    "session_id": state.session_id,
                    "name": state.name,
                    "timestamp": state.timestamp,
                    "workspaces": state.workspaces,
                    "browser_url": state.browser_url,
                    "window_geometry": state.window_geometry,
                    "agent_sessions": state.agent_sessions
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving session: {e}")

    def load_session(self, session_id: str) -> Optional[SessionState]:
        """Load a saved session."""
        session_file = self._sessions_dir / f"{session_id}.json"
        if not session_file.exists():
            return None

        try:
            with open(session_file, "r") as f:
                data = json.load(f)
                return SessionState(
                    session_id=data["session_id"],
                    name=data["name"],
                    timestamp=data["timestamp"],
                    workspaces=data.get("workspaces", []),
                    browser_url=data.get("browser_url"),
                    window_geometry=data.get("window_geometry"),
                    agent_sessions=data.get("agent_sessions", [])
                )
        except Exception as e:
            logger.error(f"Error loading session: {e}")
            return None

    def list_sessions(self) -> List[SessionState]:
        """List all saved sessions."""
        sessions = []
        for session_file in self._sessions_dir.glob("*.json"):
            try:
                with open(session_file, "r") as f:
                    data = json.load(f)
                    sessions.append(SessionState(
                        session_id=data["session_id"],
                        name=data["name"],
                        timestamp=data["timestamp"]
                    ))
            except Exception:
                continue

        return sorted(sessions, key=lambda s: s.timestamp, reverse=True)

    def get_last_session(self) -> Optional[SessionState]:
        """Get the most recent session."""
        sessions = self.list_sessions()
        return sessions[0] if sessions else None

    def delete_session(self, session_id: str):
        """Delete a saved session."""
        session_file = self._sessions_dir / f"{session_id}.json"
        if session_file.exists():
            session_file.unlink()

    def capture_current_state(self, app) -> SessionState:
        """Capture the current application state."""
        # This would be called before quitting to save state
        state = SessionState(
            session_id=f"session_{int(datetime.now().timestamp())}",
            name="Auto-save",
            timestamp=datetime.now().timestamp()
        )

        # Capture workspaces, panes, etc.
        # This is a placeholder - actual implementation would
        # query the application state

        return state

    def restore_session(self, state: SessionState, app) -> bool:
        """Restore a session."""
        try:
            # This would be called on startup to restore state
            # It would recreate workspaces, panes, etc.

            logger.info(f"Restoring session: {state.name}")

            # Restore workspaces
            for ws_data in state.workspaces:
                # Create workspace with saved configuration
                pass

            # Restore browser URL
            if state.browser_url:
                # Navigate browser to saved URL
                pass

            # Restore window geometry
            if state.window_geometry:
                # Set window size and position
                pass

            return True
        except Exception as e:
            logger.error(f"Error restoring session: {e}")
            return False


def setup_session_reopen(accel_group, reopen_manager: SessionReopenManager, app):
    """Set up keyboard shortcuts for session reopen."""
    # Ctrl+Shift+O: Reopen last session
    keyval, mods = Gtk.accelerator_parse("<Ctrl><Shift>o")
    if keyval:
        accel_group.connect(
            keyval, mods, Gtk.AccelFlags.VISIBLE,
            lambda *_: _reopen_last_session(reopen_manager, app)
        )


def _reopen_last_session(reopen_manager: SessionReopenManager, app):
    """Reopen the last session."""
    last_session = reopen_manager.get_last_session()
    if last_session:
        reopen_manager.restore_session(last_session, app)


# Import Gtk for accelerator parsing
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
