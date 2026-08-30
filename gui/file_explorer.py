"""File explorer sidebar for lmux — navigate project files with git status."""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Gio
import os
import subprocess
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("lmux.file_explorer")


class GitStatus(Enum):
    """Git file status indicators."""
    MODIFIED = "M"
    ADDED = "A"
    DELETED = "D"
    RENAMED = "R"
    COPIED = "C"
    UNTRACKED = "?"
    IGNORED = "!"
    CONFLICT = "U"
    UNCHANGED = " "


@dataclass
class FileNode:
    """Represents a file or directory in the explorer."""
    path: str
    name: str
    is_dir: bool
    git_status: GitStatus = GitStatus.UNCHANGED
    children: List["FileNode"] = None
    expanded: bool = False

    def __post_init__(self):
        if self.children is None:
            self.children = []


class GitManager:
    """Manage git operations for the file explorer."""

    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        self._status_cache: Dict[str, GitStatus] = {}
        self._ignored_files: Set[str] = set()

    def get_status(self) -> Dict[str, GitStatus]:
        """Get git status for all files in the repository."""
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return {}

            status_map = {}
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                # Parse porcelain status: XY filename
                status_char = line[0] if len(line) > 0 else " "
                filepath = line[3:].strip() if len(line) > 3 else ""

                if filepath:
                    # Handle renamed files
                    if status_char == "R" and " -> " in filepath:
                        _, new_path = filepath.split(" -> ", 1)
                        filepath = new_path

                    # Get status enum
                    try:
                        status = GitStatus(status_char)
                    except ValueError:
                        status = GitStatus.MODIFIED

                    status_map[filepath] = status

            self._status_cache = status_map
            return status_map

        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.debug(f"Git status failed: {e}")
            return {}

    def get_ignored_files(self) -> Set[str]:
        """Get list of ignored files."""
        try:
            result = subprocess.run(
                ["git", "ls-files", "--ignored", "--exclude-standard"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                self._ignored_files = set(result.stdout.strip().split("\n"))
            return self._ignored_files
        except Exception:
            return set()

    def is_git_repo(self) -> bool:
        """Check if the path is a git repository."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=self.repo_path,
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def get_branch(self) -> str:
        """Get current git branch."""
        try:
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""


class FileNodeItem(Gtk.ListBoxRow):
    """A row representing a file or directory."""

    def __init__(self, node: FileNode, depth: int = 0):
        super().__init__()
        self.node = node
        self.depth = depth

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        box.set_margin_start(depth * 16 + 8)
        box.set_margin_end(8)
        box.set_margin_top(2)
        box.set_margin_bottom(2)

        # Expand/collapse indicator for directories
        if node.is_dir:
            self._expand_label = Gtk.Label(label="▶" if not node.expanded else "▼")
            self._expand_label.set_size_request(16, -1)
            self._expand_label.set_xalign(0.5)
            box.pack_start(self._expand_label, False, False, 0)
        else:
            # Spacer for files
            spacer = Gtk.Label(label="  ")
            spacer.set_size_request(16, -1)
            box.pack_start(spacer, False, False, 0)

        # File/directory icon
        icon = "📁" if node.is_dir else self._get_file_icon(node.name)
        icon_label = Gtk.Label(label=icon)
        icon_label.set_size_request(20, -1)
        box.pack_start(icon_label, False, False, 0)

        # Name
        self._name_label = Gtk.Label(label=node.name, xalign=0)
        self._name_label.set_ellipsize(3)  # Pango.EllipsizeMode.END
        box.pack_start(self._name_label, True, True, 0)

        # Git status indicator
        if node.git_status != GitStatus.UNCHANGED:
            status_color = self._get_status_color(node.git_status)
            status_label = Gtk.Label(label=node.git_status.value)
            status_label.set_markup(f'<span foreground="{status_color}">{node.git_status.value}</span>')
            status_label.set_size_request(16, -1)
            box.pack_end(status_label, False, False, 0)

        self.add(box)

    def _get_file_icon(self, filename: str) -> str:
        """Get icon for file type."""
        ext = Path(filename).suffix.lower()
        icon_map = {
            ".py": "🐍",
            ".js": "📜",
            ".ts": "📜",
            ".tsx": "📜",
            ".jsx": "📜",
            ".html": "🌐",
            ".css": "🎨",
            ".json": "📋",
            ".md": "📝",
            ".txt": "📄",
            ".c": "⚙️",
            ".h": "⚙️",
            ".rs": "🦀",
            ".go": "🐹",
            ".java": "☕",
            ".rb": "💎",
            ".sh": "🖥️",
            ".bash": "🖥️",
            ".zsh": "🖥️",
            ".gitignore": "🔒",
            ".env": "🔐",
            "Makefile": "🔨",
            "Dockerfile": "🐳",
        }
        return icon_map.get(ext, "📄")

    def _get_status_color(self, status: GitStatus) -> str:
        """Get color for git status."""
        color_map = {
            GitStatus.MODIFIED: "#ffa500",  # Orange
            GitStatus.ADDED: "#00ff00",     # Green
            GitStatus.DELETED: "#ff0000",   # Red
            GitStatus.RENAMED: "#00bfff",   # Light blue
            GitStatus.COPIED: "#00bfff",    # Light blue
            GitStatus.UNTRACKED: "#ffff00", # Yellow
            GitStatus.IGNORED: "#808080",   # Gray
            GitStatus.CONFLICT: "#ff0000",  # Red
        }
        return color_map.get(status, "#ffffff")


class FileExplorer(Gtk.Box):
    """File explorer sidebar with git status integration."""

    def __init__(self, parent_window=None, initial_path: str = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._parent_window = parent_window
        self._current_path = initial_path or os.path.expanduser("~")
        self._git_manager: Optional[GitManager] = None
        self._root_node: Optional[FileNode] = None
        self._selected_path: Optional[str] = None

        self._setup_ui()
        self._load_directory(self._current_path)

    def _setup_ui(self):
        """Build the file explorer UI."""
        # Header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header_box.set_margin_start(8)
        header_box.set_margin_end(8)
        header_box.set_margin_top(8)
        header_box.set_margin_bottom(8)

        title = Gtk.Label(label="Files", xalign=0)
        ctx = title.get_style_context()
        ctx.add_class("workspace-header")
        header_box.pack_start(title, True, True, 0)

        # Path display
        self._path_label = Gtk.Label(label=self._current_path, xalign=0)
        self._path_label.set_ellipsize(3)
        self._path_label.set_opacity(0.7)
        header_box.pack_start(self._path_label, True, True, 0)

        # Refresh button
        refresh_btn = Gtk.Button(label="↻")
        refresh_btn.set_size_request(28, 28)
        refresh_btn.connect("clicked", lambda _: self._refresh())
        header_box.pack_end(refresh_btn, False, False, 0)

        # Up button
        up_btn = Gtk.Button(label="↑")
        up_btn.set_size_request(28, 28)
        up_btn.connect("clicked", lambda _: self._go_up())
        header_box.pack_end(up_btn, False, False, 0)

        self.pack_start(header_box, False, False, 0)

        # Search entry
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        search_box.set_margin_start(8)
        search_box.set_margin_end(8)
        search_box.set_margin_bottom(8)

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text("Search files...")
        self._search_entry.connect("search-changed", self._on_search_changed)
        search_box.pack_start(self._search_entry, True, True, 0)

        self.pack_start(search_box, False, False, 0)

        # File tree
        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._listbox.connect("row-activated", self._on_row_activated)
        self.pack_start(self._listbox, True, True, 0)

        # Status bar
        self._status_bar = Gtk.Label(label="")
        self._status_bar.set_xalign(0)
        self._status_bar.set_margin_start(8)
        self._status_bar.set_margin_end(8)
        self._status_bar.set_opacity(0.7)
        self.pack_start(self._status_bar, False, False, 2)

    def _load_directory(self, path: str):
        """Load directory contents."""
        self._current_path = path
        self._path_label.set_text(path)

        # Check for git repo
        self._git_manager = GitManager(path)
        if self._git_manager.is_git_repo():
            self._git_manager.get_status()
            branch = self._git_manager.get_branch()
            self._status_bar.set_text(f"Git: {branch}" if branch else "Git repo")
        else:
            self._status_bar.set_text("")

        # Build file tree
        self._root_node = self._build_tree(path)
        self._display_tree(self._root_node)

    def _build_tree(self, path: str) -> FileNode:
        """Build file tree from path."""
        try:
            entries = sorted(os.listdir(path), key=lambda x: (not os.path.isdir(os.path.join(path, x)), x.lower()))
        except PermissionError:
            entries = []

        node = FileNode(
            path=path,
            name=os.path.basename(path) or path,
            is_dir=True,
        )

        # Get git status
        git_status = {}
        if self._git_manager and self._git_manager.is_git_repo():
            git_status = self._git_manager.get_status()

        # Get ignored files
        ignored = self._git_manager.get_ignored_files() if self._git_manager else set()

        for entry in entries:
            entry_path = os.path.join(path, entry)

            # Skip hidden files and ignored files
            if entry.startswith(".") and entry != ".gitignore":
                continue
            if entry_path in ignored:
                continue

            is_dir = os.path.isdir(entry_path)
            status = git_status.get(entry, GitStatus.UNCHANGED)

            child = FileNode(
                path=entry_path,
                name=entry,
                is_dir=is_dir,
                git_status=status,
            )

            # Recursively build subdirectories (only one level deep for performance)
            if is_dir and not entry.startswith("."):
                child = self._build_tree(entry_path)
                child.expanded = False  # Start collapsed

            node.children.append(child)

        return node

    def _display_tree(self, node: FileNode, depth: int = 0, filter_text: str = ""):
        """Display the file tree in the listbox."""
        self._listbox.foreach(lambda child: self._listbox.remove(child))

        def _add_nodes(parent_node: FileNode, current_depth: int):
            for child in parent_node.children:
                # Apply filter
                if filter_text and not filter_text.lower() in child.name.lower():
                    if not child.is_dir:
                        continue
                    # Check if any child matches
                    if not any(filter_text.lower() in c.name.lower() for c in child.children):
                        continue

                row = FileNodeItem(child, current_depth)
                self._listbox.add(row)

                # Recursively add expanded subdirectories
                if child.is_dir and child.expanded:
                    _add_nodes(child, current_depth + 1)

        _add_nodes(node, depth)
        self._listbox.show_all()

    def _on_row_activated(self, listbox, row):
        """Handle row activation (double-click)."""
        if isinstance(row, FileNodeItem):
            node = row.node
            if node.is_dir:
                # Toggle expand/collapse
                node.expanded = not node.expanded
                self._display_tree(self._root_node, filter_text=self._search_entry.get_text())
            else:
                # Handle based on double-click action
                action = self.get_double_click_action()
                if action == "insert_path":
                    self.insert_path_to_terminal(node.path)
                elif action == "open_in_editor":
                    self.open_in_editor(node.path)
                else:
                    # Default: open file
                    self._open_file(node.path)

    def _open_file(self, path: str):
        """Open a file (emit signal for parent to handle)."""
        self.emit("file-selected", path)

    def _on_search_changed(self, entry):
        """Handle search text changes."""
        filter_text = entry.get_text()
        self._display_tree(self._root_node, filter_text=filter_text)

    def _go_up(self):
        """Navigate to parent directory."""
        parent = os.path.dirname(self._current_path)
        if parent and parent != self._current_path:
            self._load_directory(parent)

    def _refresh(self):
        """Refresh the current directory."""
        self._load_directory(self._current_path)

    def navigate_to(self, path: str):
        """Navigate to a specific path."""
        if os.path.isdir(path):
            self._load_directory(path)

    def get_selected_path(self) -> Optional[str]:
        """Get the currently selected file path."""
        return self._selected_path

    def set_double_click_action(self, action: str):
        """Set the double-click action for files.
        
        Actions: 'open', 'insert_path', 'open_in_editor'
        """
        self._double_click_action = action

    def get_double_click_action(self) -> str:
        """Get the current double-click action."""
        return getattr(self, '_double_click_action', 'open')

    def insert_path_to_terminal(self, path: str):
        """Insert file path into the focused terminal."""
        if self._parent_window and hasattr(self._parent_window, '_current_terminal'):
            term = self._parent_window._current_terminal
            if term:
                # Escape special characters for shell
                escaped = path.replace("'", "'\\''")
                term.feed_child(f"'{escaped}'", -1)

    def open_in_editor(self, path: str):
        """Open file in default editor."""
        import subprocess
        try:
            subprocess.Popen(["xdg-open", path])
        except Exception as e:
            logger.error(f"Error opening file: {e}")


# Define signals
GObject.signal_new("file-selected", FileExplorer, GObject.SIGNAL_RUN_LAST, None, (str,))


def add_file_explorer_to_sidebar(sidebar, parent_window, initial_path: str = None):
    """Add file explorer panel to the sidebar."""
    # Create a notebook for tabs
    notebook = Gtk.Notebook()

    # File explorer panel
    explorer = FileExplorer(parent_window, initial_path)
    notebook.append_page(explorer, Gtk.Label(label="Files"))

    # Add to sidebar
    sidebar.pack_end(notebook, True, True, 0)
    sidebar.show_all()

    return explorer
