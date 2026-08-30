"""Agent session panels for lmux — web-rendered UI with hibernation."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
import logging
import json
import os
import signal
import subprocess
from pathlib import Path
from typing import List, Optional, Dict, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

logger = logging.getLogger("lmux.agent_sessions")


class AgentState(Enum):
    """Agent session states."""
    RUNNING = "running"
    HIBERNATED = "hibernated"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class AgentSession:
    """Represents an agent session."""
    session_id: str
    agent_type: str
    workspace_id: str
    pane_id: str
    state: AgentState = AgentState.RUNNING
    pid: Optional[int] = None
    created_at: float = 0.0
    last_active: float = 0.0
    hibernated_at: Optional[float] = None
    resume_command: Optional[str] = None
    conversation_history: List[Dict[str, str]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = datetime.now().timestamp()
        if self.last_active == 0.0:
            self.last_active = datetime.now().timestamp()


class AgentSessionManager:
    """Manages agent sessions with hibernation support."""

    def __init__(self, config_dir: Optional[str] = None):
        self._config_dir = Path(config_dir or "~/.config/lmux").expanduser()
        self._sessions_file = self._config_dir / "agent_sessions.json"
        self._sessions: Dict[str, AgentSession] = {}
        self._listeners: List[Callable] = []
        self._load_sessions()

    def _load_sessions(self):
        """Load sessions from file."""
        if self._sessions_file.exists():
            try:
                with open(self._sessions_file, "r") as f:
                    data = json.load(f)
                    for sid, session_data in data.items():
                        session_data["state"] = AgentState(session_data["state"])
                        self._sessions[sid] = AgentSession(**session_data)
            except Exception as e:
                logger.error(f"Error loading agent sessions: {e}")

    def _save_sessions(self):
        """Save sessions to file."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        data = {}
        for sid, session in self._sessions.items():
            data[sid] = {
                "session_id": session.session_id,
                "agent_type": session.agent_type,
                "workspace_id": session.workspace_id,
                "pane_id": session.pane_id,
                "state": session.state.value,
                "pid": session.pid,
                "created_at": session.created_at,
                "last_active": session.last_active,
                "hibernated_at": session.hibernated_at,
                "resume_command": session.resume_command,
                "conversation_history": session.conversation_history[-50:],  # Keep last 50
                "metadata": session.metadata
            }
        with open(self._sessions_file, "w") as f:
            json.dump(data, f, indent=2)

    def create_session(
        self,
        agent_type: str,
        workspace_id: str,
        pane_id: str,
        resume_command: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> AgentSession:
        """Create a new agent session."""
        session_id = f"{agent_type}_{workspace_id}_{pane_id}_{int(datetime.now().timestamp())}"

        session = AgentSession(
            session_id=session_id,
            agent_type=agent_type,
            workspace_id=workspace_id,
            pane_id=pane_id,
            resume_command=resume_command,
            metadata=metadata or {}
        )

        self._sessions[session_id] = session
        self._save_sessions()
        self._notify_listeners("created", session)

        return session

    def hibernate(self, session_id: str) -> bool:
        """Hibernate an agent session."""
        session = self._sessions.get(session_id)
        if not session or session.state != AgentState.RUNNING:
            return False

        # Save conversation state
        self._save_conversation_state(session)

        # Stop the agent process
        if session.pid:
            try:
                os.kill(session.pid, signal.SIGTERM)
                os.waitpid(session.pid, os.WNOHANG)
            except (ProcessLookupError, ChildProcessError):
                pass

        session.state = AgentState.HIBERNATED
        session.hibernated_at = datetime.now().timestamp()
        self._save_sessions()
        self._notify_listeners("hibernated", session)

        return True

    def resume(self, session_id: str) -> bool:
        """Resume a hibernated agent session."""
        session = self._sessions.get(session_id)
        if not session or session.state != AgentState.HIBERNATED:
            return False

        # Restore conversation state
        self._restore_conversation_state(session)

        # Start the agent process
        if session.resume_command:
            try:
                cmd = session.resume_command.split()
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                session.pid = proc.pid
            except Exception as e:
                logger.error(f"Error resuming agent: {e}")
                session.state = AgentState.ERROR
                self._save_sessions()
                return False

        session.state = AgentState.RUNNING
        session.hibernated_at = None
        session.last_active = datetime.now().timestamp()
        self._save_sessions()
        self._notify_listeners("resumed", session)

        return True

    def stop(self, session_id: str) -> bool:
        """Stop an agent session."""
        session = self._sessions.get(session_id)
        if not session:
            return False

        # Stop the process
        if session.pid and session.state == AgentState.RUNNING:
            try:
                os.kill(session.pid, signal.SIGTERM)
                os.waitpid(session.pid, os.WNOHANG)
            except (ProcessLookupError, ChildProcessError):
                pass

        session.state = AgentState.STOPPED
        self._save_sessions()
        self._notify_listeners("stopped", session)

        return True

    def fork(self, session_id: str) -> Optional[AgentSession]:
        """Fork an agent session (create a copy)."""
        original = self._sessions.get(session_id)
        if not original:
            return None

        new_session = self.create_session(
            agent_type=original.agent_type,
            workspace_id=original.workspace_id,
            pane_id=f"{original.pane_id}_fork",
            resume_command=original.resume_command,
            metadata={**original.metadata, "forked_from": session_id}
        )

        # Copy conversation history
        new_session.conversation_history = original.conversation_history.copy()

        self._save_sessions()
        self._notify_listeners("forked", new_session)

        return new_session

    def list_sessions(
        self,
        agent_type: Optional[str] = None,
        workspace_id: Optional[str] = None,
        state: Optional[AgentState] = None
    ) -> List[AgentSession]:
        """List sessions with optional filters."""
        sessions = list(self._sessions.values())

        if agent_type:
            sessions = [s for s in sessions if s.agent_type == agent_type]
        if workspace_id:
            sessions = [s for s in sessions if s.workspace_id == workspace_id]
        if state:
            sessions = [s for s in sessions if s.state == state]

        return sessions

    def get_session(self, session_id: str) -> Optional[AgentSession]:
        """Get a specific session."""
        return self._sessions.get(session_id)

    def add_conversation_entry(
        self,
        session_id: str,
        role: str,
        content: str
    ):
        """Add an entry to the conversation history."""
        session = self._sessions.get(session_id)
        if not session:
            return

        session.conversation_history.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().timestamp()
        })
        session.last_active = datetime.now().timestamp()
        self._save_sessions()

    def add_listener(self, callback: Callable):
        """Add a listener for session events."""
        self._listeners.append(callback)

    def _notify_listeners(self, event: str, session: AgentSession):
        """Notify listeners of session events."""
        for listener in self._listeners:
            try:
                listener(event, session)
            except Exception as e:
                logger.error(f"Error notifying listener: {e}")

    def _save_conversation_state(self, session: AgentSession):
        """Save conversation state to disk."""
        state_dir = self._config_dir / "agent_states"
        state_dir.mkdir(parents=True, exist_ok=True)

        state_file = state_dir / f"{session.session_id}.json"
        with open(state_file, "w") as f:
            json.dump({
                "conversation_history": session.conversation_history,
                "metadata": session.metadata
            }, f, indent=2)

    def _restore_conversation_state(self, session: AgentSession):
        """Restore conversation state from disk."""
        state_dir = self._config_dir / "agent_states"
        state_file = state_dir / f"{session.session_id}.json"

        if state_file.exists():
            try:
                with open(state_file, "r") as f:
                    state = json.load(f)
                    session.conversation_history = state.get("conversation_history", [])
                    session.metadata.update(state.get("metadata", {}))
            except Exception as e:
                logger.error(f"Error restoring conversation state: {e}")


class AgentSessionPanel(Gtk.Box):
    """Panel displaying agent sessions."""

    def __init__(self, manager: AgentSessionManager):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._manager = manager
        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)

        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Agent Sessions")
        title.set_xalign(0)
        title.get_style_context().add_class("heading")
        header.pack_start(title, True, True, 0)

        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", lambda _: self.refresh())
        header.pack_end(refresh_btn, False, False, 0)

        hibernate_all_btn = Gtk.Button(label="Hibernate All")
        hibernate_all_btn.connect("clicked", lambda _: self._hibernate_all())
        header.pack_end(hibernate_all_btn, False, False, 0)

        self.pack_start(header, False, False, 0)

        # Scrolled list
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self._list)
        self.pack_start(scroll, True, True, 0)

        # Connect to manager events
        manager.add_listener(self._on_session_event)

        self.refresh()

    def refresh(self):
        """Refresh the session list."""
        for child in self._list.get_children():
            self._list.remove(child)

        sessions = self._manager.list_sessions()
        for session in sessions:
            row = self._create_row(session)
            self._list.add(row)

        self._list.show_all()

    def _create_row(self, session: AgentSession) -> Gtk.ListBoxRow:
        """Create a row for a session."""
        row = Gtk.ListBoxRow()
        row._session = session

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        # State indicator
        state_colors = {
            AgentState.RUNNING: "#4CAF50",
            AgentState.HIBERNATED: "#FFC107",
            AgentState.STOPPED: "#9E9E9E",
            AgentState.ERROR: "#F44336"
        }
        state_labels = {
            AgentState.RUNNING: "●",
            AgentState.HIBERNATED: "◐",
            AgentState.STOPPED: "○",
            AgentState.ERROR: "✗"
        }

        indicator = Gtk.Label(label=state_labels[session.state])
        indicator.get_style_context().add_class("state-indicator")
        box.pack_start(indicator, False, False, 0)

        # Session info
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        agent_label = Gtk.Label(label=f"{session.agent_type} — {session.session_id[:12]}...")
        agent_label.set_xalign(0)
        info.pack_start(agent_label, False, False, 0)

        meta_parts = [f"ws:{session.workspace_id}"]
        if session.state == AgentState.HIBERNATED:
            meta_parts.append("hibernated")
        elif session.pid:
            meta_parts.append(f"pid:{session.pid}")

        meta = Gtk.Label(label=" · ".join(meta_parts))
        meta.set_xalign(0)
        meta.get_style_context().add_class("dim-label")
        meta.get_style_context().add_class("small")
        info.pack_start(meta, False, False, 0)

        box.pack_start(info, True, True, 0)

        # Actions
        if session.state == AgentState.RUNNING:
            hibernate_btn = Gtk.Button(label="Hibernate")
            hibernate_btn.connect("clicked", lambda _, s=session: (
                self._manager.hibernate(s.session_id),
                self.refresh()
            ))
            box.pack_end(hibernate_btn, False, False, 0)

            stop_btn = Gtk.Button(label="Stop")
            stop_btn.connect("clicked", lambda _, s=session: (
                self._manager.stop(s.session_id),
                self.refresh()
            ))
            box.pack_end(stop_btn, False, False, 0)

        elif session.state == AgentState.HIBERNATED:
            resume_btn = Gtk.Button(label="Resume")
            resume_btn.connect("clicked", lambda _, s=session: (
                self._manager.resume(s.session_id),
                self.refresh()
            ))
            box.pack_end(resume_btn, False, False, 0)

        fork_btn = Gtk.Button(label="Fork")
        fork_btn.connect("clicked", lambda _, s=session: (
            self._manager.fork(s.session_id),
            self.refresh()
        ))
        box.pack_end(fork_btn, False, False, 0)

        row.add(box)
        return row

    def _hibernate_all(self):
        """Hibernate all running sessions."""
        sessions = self._manager.list_sessions(state=AgentState.RUNNING)
        for session in sessions:
            self._manager.hibernate(session.session_id)
        self.refresh()

    def _on_session_event(self, event: str, session: AgentSession):
        """Handle session events."""
        # Refresh on any event
        GLib.idle_add(self.refresh)


class AgentConversationView(Gtk.Box):
    """View for displaying agent conversation history."""

    def __init__(self, manager: AgentSessionManager):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._manager = manager
        self._current_session: Optional[AgentSession] = None

        # Session selector
        self._session_combo = Gtk.ComboBoxText()
        self._session_combo.connect("changed", self._on_session_changed)
        self.pack_start(self._session_combo, False, False, 0)

        # Conversation view
        self._conversation_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self._conversation_box)
        self.pack_start(scroll, True, True, 0)

        # Input area
        input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._input = Gtk.Entry()
        self._input.set_placeholder_text("Type a message...")
        self._input.connect("activate", self._on_send)
        input_box.pack_start(self._input, True, True, 0)

        send_btn = Gtk.Button(label="Send")
        send_btn.connect("clicked", lambda _: self._on_send(None))
        input_box.pack_end(send_btn, False, False, 0)

        self.pack_start(input_box, False, False, 0)

        self._update_session_list()

    def _update_session_list(self):
        """Update the session dropdown."""
        self._session_combo.remove_all()
        sessions = self._manager.list_sessions()

        for session in sessions:
            label = f"{session.agent_type} — {session.session_id[:16]}..."
            self._session_combo.append(session.session_id, label)

        if sessions:
            self._session_combo.set_active_id(sessions[0].session_id)

    def _on_session_changed(self, combo):
        """Handle session selection change."""
        session_id = combo.get_active_id()
        if session_id:
            self._current_session = self._manager.get_session(session_id)
            self._refresh_conversation()

    def _refresh_conversation(self):
        """Refresh the conversation display."""
        for child in self._conversation_box.get_children():
            self._conversation_box.remove(child)

        if not self._current_session:
            return

        for entry in self._current_session.conversation_history:
            self._add_message(entry["role"], entry["content"])

    def _add_message(self, role: str, content: str):
        """Add a message to the conversation view."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        # Role label
        role_label = Gtk.Label(label=role.upper())
        role_label.set_xalign(0 if role == "user" else 1)
        role_label.get_style_context().add_class("dim-label")
        role_label.get_style_context().add_class("small")
        box.pack_start(role_label, False, False, 0)

        # Content
        content_label = Gtk.Label(label=content)
        content_label.set_xalign(0 if role == "user" else 1)
        content_label.set_line_wrap(True)
        content_label.set_selectable(True)
        box.pack_start(content_label, False, False, 0)

        self._conversation_box.pack_start(box, False, False, 4)

    def _on_send(self, entry):
        """Handle message send."""
        if not self._current_session:
            return

        text = entry.get_text() if entry else self._input.get_text()
        if not text:
            return

        # Add to conversation
        self._manager.add_conversation_entry(
            self._current_session.session_id,
            "user",
            text
        )

        # Display
        self._add_message("user", text)
        self._input.set_text("")

        # Scroll to bottom
        adjustment = self._conversation_box.get_parent().get_vadjustment()
        adjustment.set_value(adjustment.get_upper())
