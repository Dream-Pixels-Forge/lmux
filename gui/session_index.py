"""Session index panel for lmux — searchable session list."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import json
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger("lmux.session_index")


@dataclass
class SessionInfo:
    """Information about a saved session."""
    session_id: str
    name: str
    created_at: float
    last_modified: float
    workspace_count: int
    surface_count: int
    pane_count: int
    agent_type: Optional[str] = None
    tags: List[str] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


class SessionIndexManager:
    """Manages the session index for searchable session list."""

    def __init__(self, config_dir: Optional[str] = None):
        self._config_dir = Path(config_dir or "~/.config/lmux").expanduser()
        self._index_file = self._config_dir / "session_index.json"
        self._sessions: Dict[str, SessionInfo] = {}
        self._load_index()

    def _load_index(self):
        """Load session index from file."""
        if self._index_file.exists():
            try:
                with open(self._index_file, "r") as f:
                    data = json.load(f)
                    for sid, session_data in data.items():
                        self._sessions[sid] = SessionInfo(**session_data)
            except Exception as e:
                logger.error(f"Error loading session index: {e}")

    def _save_index(self):
        """Save session index to file."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        data = {}
        for sid, session in self._sessions.items():
            data[sid] = {
                "session_id": session.session_id,
                "name": session.name,
                "created_at": session.created_at,
                "last_modified": session.last_modified,
                "workspace_count": session.workspace_count,
                "surface_count": session.surface_count,
                "pane_count": session.pane_count,
                "agent_type": session.agent_type,
                "tags": session.tags
            }
        with open(self._index_file, "w") as f:
            json.dump(data, f, indent=2)

    def add_session(
        self,
        session_id: str,
        name: str,
        workspace_count: int = 0,
        surface_count: int = 0,
        pane_count: int = 0,
        agent_type: Optional[str] = None,
        tags: Optional[List[str]] = None
    ):
        """Add or update a session in the index."""
        now = datetime.now().timestamp()

        if session_id in self._sessions:
            session = self._sessions[session_id]
            session.name = name
            session.last_modified = now
            session.workspace_count = workspace_count
            session.surface_count = surface_count
            session.pane_count = pane_count
            if agent_type:
                session.agent_type = agent_type
            if tags:
                session.tags = tags
        else:
            self._sessions[session_id] = SessionInfo(
                session_id=session_id,
                name=name,
                created_at=now,
                last_modified=now,
                workspace_count=workspace_count,
                surface_count=surface_count,
                pane_count=pane_count,
                agent_type=agent_type,
                tags=tags or []
            )

        self._save_index()

    def remove_session(self, session_id: str):
        """Remove a session from the index."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            self._save_index()

    def search(self, query: str) -> List[SessionInfo]:
        """Search sessions by name or tags."""
        query_lower = query.lower()
        results = []

        for session in self._sessions.values():
            if (query_lower in session.name.lower() or
                any(query_lower in tag.lower() for tag in session.tags)):
                results.append(session)

        return sorted(results, key=lambda s: s.last_modified, reverse=True)

    def list_sessions(self) -> List[SessionInfo]:
        """List all sessions."""
        return sorted(
            self._sessions.values(),
            key=lambda s: s.last_modified,
            reverse=True
        )

    def get_session(self, session_id: str) -> Optional[SessionInfo]:
        """Get a specific session."""
        return self._sessions.get(session_id)

    def add_tag(self, session_id: str, tag: str):
        """Add a tag to a session."""
        if session_id in self._sessions:
            session = self._sessions[session_id]
            if tag not in session.tags:
                session.tags.append(tag)
                self._save_index()

    def remove_tag(self, session_id: str, tag: str):
        """Remove a tag from a session."""
        if session_id in self._sessions:
            session = self._sessions[session_id]
            if tag in session.tags:
                session.tags.remove(tag)
                self._save_index()


class SessionIndexPanel(Gtk.Box):
    """Panel for browsing and searching saved sessions."""

    def __init__(self, manager: SessionIndexManager):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._manager = manager

        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Session Index")
        title.set_xalign(0)
        title.get_style_context().add_class("heading")
        header.pack_start(title, True, True, 0)

        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", lambda _: self.refresh())
        header.pack_end(refresh_btn, False, False, 0)

        self.pack_start(header, False, False, 0)

        # Search entry
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        search_box.set_margin_start(8)
        search_box.set_margin_end(8)

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text("Search sessions...")
        self._search_entry.connect("search-changed", self._on_search_changed)
        search_box.pack_start(self._search_entry, True, True, 0)

        self.pack_start(search_box, False, False, 0)

        # Session list
        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.pack_start(self._listbox, True, True, 0)

        self.refresh()

    def refresh(self):
        """Refresh the session list."""
        for child in self._listbox.get_children():
            self._listbox.remove(child)

        sessions = self._manager.list_sessions()
        for session in sessions:
            row = self._create_row(session)
            self._listbox.add(row)

        self._listbox.show_all()

    def _create_row(self, session: SessionInfo) -> Gtk.ListBoxRow:
        """Create a row for a session."""
        row = Gtk.ListBoxRow()
        row._session = session

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        # Session info
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        name_label = Gtk.Label(label=session.name)
        name_label.set_xalign(0)
        info.pack_start(name_label, False, False, 0)

        # Metadata
        meta_parts = [
            f"{session.workspace_count} ws",
            f"{session.surface_count} surfaces",
            f"{session.pane_count} panes"
        ]
        if session.agent_type:
            meta_parts.insert(0, session.agent_type)

        meta = Gtk.Label(label=" · ".join(meta_parts))
        meta.set_xalign(0)
        meta.get_style_context().add_class("dim-label")
        meta.get_style_context().add_class("small")
        info.pack_start(meta, False, False, 0)

        # Tags
        if session.tags:
            tags_label = Gtk.Label(label=f"Tags: {', '.join(session.tags)}")
            tags_label.set_xalign(0)
            tags_label.get_style_context().add_class("dim-label")
            info.pack_start(tags_label, False, False, 0)

        box.pack_start(info, True, True, 0)

        # Timestamp
        ts = datetime.fromtimestamp(session.last_modified).strftime("%Y-%m-%d %H:%M")
        ts_label = Gtk.Label(label=ts)
        ts_label.get_style_context().add_class("dim-label")
        box.pack_end(ts_label, False, False, 0)

        row.add(box)
        return row

    def _on_search_changed(self, entry):
        """Handle search text changes."""
        query = entry.get_text()
        for child in self._listbox.get_children():
            self._listbox.remove(child)

        if query:
            sessions = self._manager.search(query)
        else:
            sessions = self._manager.list_sessions()

        for session in sessions:
            row = self._create_row(session)
            self._listbox.add(row)

        self._listbox.show_all()
