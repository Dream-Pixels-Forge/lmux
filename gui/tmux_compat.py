"""tmux compatibility layer for lmux — parse tmux commands and overlay panes."""
import re
import logging
import subprocess
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass

logger = logging.getLogger("lmux.tmux_compat")


@dataclass
class TmuxPane:
    """Represents a tmux pane."""
    session: str
    window: int
    pane: int
    width: int
    height: int
    x: int
    y: int
    command: str = ""
    title: str = ""


@dataclass
class TmuxWindow:
    """Represents a tmux window."""
    session: str
    window: int
    name: str
    active: bool = False
    panes: List[TmuxPane] = None

    def __post_init__(self):
        if self.panes is None:
            self.panes = []


class TmuxResumeParser:
    """Parse tmux resume commands for session restoration."""

    @staticmethod
    def parse_resume_command(command: str) -> Optional[Dict[str, Any]]:
        """Parse a tmux resume command."""
        # tmux attach-session -t session_name
        attach_match = re.match(r"tmux\s+attach(?:-session)?\s+(?:-t\s+)?(\S+)", command)
        if attach_match:
            return {
                "type": "attach",
                "session": attach_match.group(1)
            }

        # tmux new-session -s session_name
        new_match = re.match(r"tmux\s+new-session\s+(?:-s\s+)?(\S+)", command)
        if new_match:
            return {
                "type": "new",
                "session": new_match.group(1)
            }

        # tmux switch-client -t session_name
        switch_match = re.match(r"tmux\s+switch-client\s+(?:-t\s+)?(\S+)", command)
        if switch_match:
            return {
                "type": "switch",
                "session": switch_match.group(1)
            }

        return None

    @staticmethod
    def generate_resume_command(session: str) -> str:
        """Generate tmux resume command."""
        return f"tmux attach-session -t {session}"


class TmuxCompat:
    """tmux compatibility layer."""

    def __init__(self):
        self._sessions: Dict[str, List[TmuxWindow]] = {}

    def list_sessions(self) -> List[str]:
        """List all tmux sessions."""
        try:
            result = subprocess.run(
                ["tmux", "list-sessions", "-F", "#{session_name}"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
        except Exception as e:
            logger.error(f"Error listing tmux sessions: {e}")
        return []

    def list_windows(self, session: str) -> List[TmuxWindow]:
        """List all windows in a tmux session."""
        try:
            result = subprocess.run(
                ["tmux", "list-windows", "-t", session, "-F",
                 "#{window_index}:#{window_name}:#{window_active}"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                windows = []
                for line in result.stdout.strip().split("\n"):
                    if line:
                        parts = line.split(":")
                        if len(parts) >= 3:
                            windows.append(TmuxWindow(
                                session=session,
                                window=int(parts[0]),
                                name=parts[1],
                                active=parts[2] == "1"
                            ))
                return windows
        except Exception as e:
            logger.error(f"Error listing tmux windows: {e}")
        return []

    def list_panes(self, session: str, window: Optional[int] = None) -> List[TmuxPane]:
        """List all panes in a tmux window."""
        try:
            target = session
            if window is not None:
                target = f"{session}:{window}"

            result = subprocess.run(
                ["tmux", "list-panes", "-t", target, "-F",
                 "#{pane_index}:#{pane_width}:#{pane_height}:#{pane_x}:#{pane_y}:#{pane_current_command}:#{pane_title}"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                panes = []
                for line in result.stdout.strip().split("\n"):
                    if line:
                        parts = line.split(":")
                        if len(parts) >= 7:
                            panes.append(TmuxPane(
                                session=session,
                                window=window or 0,
                                pane=int(parts[0]),
                                width=int(parts[1]),
                                height=int(parts[2]),
                                x=int(parts[3]),
                                y=int(parts[4]),
                                command=parts[5],
                                title=parts[6]
                            ))
                return panes
        except Exception as e:
            logger.error(f"Error listing tmux panes: {e}")
        return []

    def capture_pane(self, session: str, window: int, pane: int) -> Optional[str]:
        """Capture the content of a tmux pane."""
        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", f"{session}:{window}.{pane}", "-p"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout
        except Exception as e:
            logger.error(f"Error capturing tmux pane: {e}")
        return None

    def send_keys(self, session: str, window: int, pane: int, keys: str):
        """Send keys to a tmux pane."""
        try:
            subprocess.run(
                ["tmux", "send-keys", "-t", f"{session}:{window}.{pane}", keys, "Enter"],
                capture_output=True,
                timeout=5
            )
        except Exception as e:
            logger.error(f"Error sending keys to tmux pane: {e}")

    def overlay_to_lmux(self, session: str) -> bool:
        """Overlay a tmux session into lmux panes."""
        try:
            windows = self.list_windows(session)
            if not windows:
                return False

            # For now, just capture and display the pane content
            for window in windows:
                panes = self.list_panes(session, window.window)
                for pane in panes:
                    content = self.capture_pane(session, window.window, pane.pane)
                    if content:
                        logger.info(f"Captured pane {pane.pane}: {content[:100]}...")

            return True
        except Exception as e:
            logger.error(f"Error overlaying tmux session: {e}")
            return False

    def generate_lmux_command(self, tmux_command: str) -> Optional[str]:
        """Convert a tmux command to lmux equivalent."""
        parsed = TmuxResumeParser.parse_resume_command(tmux_command)
        if not parsed:
            return None

        if parsed["type"] == "attach":
            return f"lmux workspace select {parsed['session']}"
        elif parsed["type"] == "new":
            return f"lmux workspace create {parsed['session']}"
        elif parsed["type"] == "switch":
            return f"lmux workspace select {parsed['session']}"

        return None
