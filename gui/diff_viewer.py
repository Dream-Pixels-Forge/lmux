"""Diff viewer for lmux — unified diff display with syntax highlighting."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, Pango
import logging
import subprocess
import re
from typing import List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("lmux.diff_viewer")


class DiffLineType(Enum):
    """Types of diff lines."""
    HEADER = "header"
    HUNK_HEADER = "hunk_header"
    ADDITION = "addition"
    DELETION = "deletion"
    CONTEXT = "context"
    FILE_HEADER = "file_header"


@dataclass
class DiffLine:
    """A single line in a diff."""
    line_type: DiffLineType
    content: str
    old_line_num: Optional[int] = None
    new_line_num: Optional[int] = None


@dataclass
class DiffHunk:
    """A hunk in a diff."""
    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: List[DiffLine]


@dataclass
class DiffFile:
    """A file in a diff."""
    old_path: Optional[str]
    new_path: Optional[str]
    hunks: List[DiffHunk]
    is_binary: bool = False
    is_new: bool = False
    is_deleted: bool = False
    is_renamed: bool = False


class DiffParser:
    """Parse unified diff format."""

    @staticmethod
    def parse(diff_text: str) -> List[DiffFile]:
        """Parse a unified diff into DiffFile objects."""
        files = []
        current_file = None
        current_hunk = None
        old_line = 0
        new_line = 0

        for line in diff_text.split("\n"):
            # File header
            if line.startswith("diff --git"):
                if current_file:
                    files.append(current_file)
                current_file = DiffFile(old_path=None, new_path=None, hunks=[])
                continue

            if current_file is None:
                continue

            # File paths
            if line.startswith("--- a/"):
                current_file.old_path = line[6:]
                continue
            elif line.startswith("--- /dev/null"):
                current_file.is_new = True
                continue
            elif line.startswith("+++ b/"):
                current_file.new_path = line[6:]
                continue
            elif line.startswith("+++ /dev/null"):
                current_file.is_deleted = True
                continue

            # Binary file
            if line.startswith("Binary files"):
                current_file.is_binary = True
                continue

            # Hunk header
            hunk_match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if hunk_match:
                if current_hunk:
                    current_file.hunks.append(current_hunk)

                old_start = int(hunk_match.group(1))
                old_count = int(hunk_match.group(2) or "1")
                new_start = int(hunk_match.group(3))
                new_count = int(hunk_match.group(4) or "1")

                current_hunk = DiffHunk(
                    header=line,
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                    lines=[]
                )
                old_line = old_start
                new_line = new_start
                continue

            if current_hunk is None:
                continue

            # Diff lines
            if line.startswith("+"):
                current_hunk.lines.append(DiffLine(
                    line_type=DiffLineType.ADDITION,
                    content=line[1:],
                    new_line_num=new_line
                ))
                new_line += 1
            elif line.startswith("-"):
                current_hunk.lines.append(DiffLine(
                    line_type=DiffLineType.DELETION,
                    content=line[1:],
                    old_line_num=old_line
                ))
                old_line += 1
            elif line.startswith(" "):
                current_hunk.lines.append(DiffLine(
                    line_type=DiffLineType.CONTEXT,
                    content=line[1:],
                    old_line_num=old_line,
                    new_line_num=new_line
                ))
                old_line += 1
                new_line += 1
            elif line.startswith("\\"):
                current_hunk.lines.append(DiffLine(
                    line_type=DiffLineType.HEADER,
                    content=line
                ))

        # Don't forget the last file/hunk
        if current_hunk and current_file:
            current_file.hunks.append(current_hunk)
        if current_file:
            files.append(current_file)

        return files


class DiffView(Gtk.ScrolledWindow):
    """Widget for displaying unified diffs."""

    def __init__(self):
        super().__init__()
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self._text_view = Gtk.TextView()
        self._text_view.set_editable(False)
        self._text_view.set_cursor_visible(False)
        self._text_view.set_monospace(True)
        self._text_view.set_left_margin(8)
        self._text_view.set_right_margin(8)

        self._buffer = self._text_view.get_buffer()
        self._create_tags()

        self.add(self._text_view)

    def _create_tags(self):
        """Create text tags for syntax highlighting."""
        # Colors
        colors = {
            "header": {"foreground": "#6C7A89", "weight": Pango.Weight.BOLD},
            "hunk_header": {"foreground": "#8E44AD", "weight": Pango.Weight.BOLD},
            "file_header": {"foreground": "#2980B9", "weight": Pango.Weight.BOLD},
            "addition": {"foreground": "#27AE60", "background": "#E8F5E9"},
            "deletion": {"foreground": "#C0392B", "background": "#FFEBEE"},
            "context": {"foreground": "#2C3E50"},
            "line_number": {"foreground": "#95A5A6"},
        }

        for name, props in colors.items():
            tag = self._buffer.create_tag(name)
            for prop, value in props.items():
                if prop == "weight":
                    tag.set_property("weight", value)
                else:
                    tag.set_property(prop, value)

    def set_diff(self, diff_text: str):
        """Display a unified diff."""
        self._buffer.set_text("")

        files = DiffParser.parse(diff_text)

        for file in files:
            self._add_file_header(file)

            for hunk in file.hunks:
                self._add_hunk(hunk)

    def set_diff_from_git(self, args: str = "") -> bool:
        """Load diff from git."""
        try:
            cmd = ["git", "diff"] + (args.split() if args else [])
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0 and result.stdout:
                self.set_diff(result.stdout)
                return True
            else:
                self._buffer.set_text(f"No changes or error: {result.stderr}")
                return False
        except Exception as e:
            self._buffer.set_text(f"Error running git diff: {e}")
            return False

    def set_diff_from_file(self, file_path: str) -> bool:
        """Load diff from a file."""
        try:
            with open(file_path, "r") as f:
                content = f.read()
                self.set_diff(content)
                return True
        except Exception as e:
            self._buffer.set_text(f"Error reading file: {e}")
            return False

    def _add_file_header(self, file: DiffFile):
        """Add a file header to the view."""
        end = self._buffer.get_end_iter()

        if file.is_new:
            label = f"new file: {file.new_path}"
        elif file.is_deleted:
            label = f"deleted file: {file.old_path}"
        elif file.is_renamed:
            label = f"renamed: {file.old_path} -> {file.new_path}"
        else:
            label = f"diff --git {file.old_path} {file.new_path}"

        self._buffer.insert_with_tags_by_name(end, f"\n{label}\n", "file_header")

        if file.is_binary:
            self._buffer.insert_with_tags_by_name(end, "Binary files differ\n", "header")

    def _add_hunk(self, hunk: DiffHunk):
        """Add a hunk to the view."""
        end = self._buffer.get_end_iter()

        # Hunk header
        self._buffer.insert_with_tags_by_name(end, f"{hunk.header}\n", "hunk_header")

        # Lines
        for line in hunk.lines:
            self._add_line(line)

    def _add_line(self, line: DiffLine):
        """Add a diff line to the view."""
        end = self._buffer.get_end_iter()

        # Line numbers
        old_num = f"{line.old_line_num:>4}" if line.old_line_num else "    "
        new_num = f"{line.new_line_num:>4}" if line.new_line_num else "    "

        self._buffer.insert_with_tags_by_name(end, f"{old_num} ", "line_number")
        self._buffer.insert_with_tags_by_name(end, f"{new_num} ", "line_number")

        # Prefix
        prefix = {
            DiffLineType.ADDITION: "+",
            DiffLineType.DELETION: "-",
            DiffLineType.CONTEXT: " ",
            DiffLineType.HEADER: "\\"
        }.get(line.line_type, " ")

        # Content with appropriate tag
        tag_name = line.line_type.value
        self._buffer.insert_with_tags_by_name(end, f"{prefix}{line.content}\n", tag_name)

    def clear(self):
        """Clear the diff view."""
        self._buffer.set_text("")

    def get_text(self) -> str:
        """Get the full text content."""
        start = self._buffer.get_start_iter()
        end = self._buffer.get_end_iter()
        return self._buffer.get_text(start, end, False)


class DiffViewerPanel(Gtk.Box):
    """Panel containing diff viewer with controls."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        # Header with controls
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_margin_start(8)
        header.set_margin_end(8)
        header.set_margin_top(4)

        title = Gtk.Label(label="Diff Viewer")
        title.set_xalign(0)
        title.get_style_context().add_class("heading")
        header.pack_start(title, True, True, 0)

        # Git diff button
        git_diff_btn = Gtk.Button(label="Git Diff")
        git_diff_btn.connect("clicked", lambda _: self._diff_view.set_diff_from_git())
        header.pack_end(git_diff_btn, False, False, 0)

        # Stage all button
        stage_all_btn = Gtk.Button(label="Stage All")
        stage_all_btn.connect("clicked", lambda _: self._stage_all())
        header.pack_end(stage_all_btn, False, False, 0)

        # Unstage all button
        unstage_all_btn = Gtk.Button(label="Unstage All")
        unstage_all_btn.connect("clicked", lambda _: self._unstage_all())
        header.pack_end(unstage_all_btn, False, False, 0)

        self.pack_start(header, False, False, 0)

        # Diff view
        self._diff_view = DiffView()
        self.pack_start(self._diff_view, True, True, 0)

        # Status bar
        self._status = Gtk.Label(label="No diff loaded")
        self._status.set_xalign(0)
        self._status.set_margin_start(8)
        self.pack_end(self._status, False, False, 0)

    def load_diff(self, diff_text: str):
        """Load diff text."""
        self._diff_view.set_diff(diff_text)
        self._update_status()

    def load_git_diff(self, args: str = ""):
        """Load git diff."""
        self._diff_view.set_diff_from_git(args)
        self._update_status()

    def load_file(self, file_path: str):
        """Load diff from file."""
        self._diff_view.set_diff_from_file(file_path)
        self._update_status()

    def _update_status(self):
        """Update status bar."""
        text = self._diff_view.get_text()
        lines = text.split("\n")
        additions = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
        deletions = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
        self._status.set_text(f"+{additions} -{deletions}")

    def _stage_all(self):
        """Stage all changes."""
        try:
            subprocess.run(["git", "add", "-A"], capture_output=True, timeout=5)
            self.load_git_diff()
        except Exception as e:
            logger.error(f"Error staging: {e}")

    def _unstage_all(self):
        """Unstage all changes."""
        try:
            subprocess.run(["git", "reset", "HEAD"], capture_output=True, timeout=5)
            self.load_git_diff()
        except Exception as e:
            logger.error(f"Error unstaging: {e}")
