"""Focus history tracking for lmux.

Maintains a stack of recent focus events (which pane/workspace was focused)
for quick navigation and UI display.
"""
import threading
import time


class FocusEntry:
    """A single focus history entry."""

    __slots__ = ("pane_id", "workspace_id", "surface_id", "timestamp", "label")

    def __init__(self, pane_id=None, workspace_id=None, surface_id=None, label=None):
        self.pane_id = pane_id
        self.workspace_id = workspace_id
        self.surface_id = surface_id
        self.timestamp = time.time()
        self.label = label or ""

    def to_dict(self):
        return {
            "pane_id": self.pane_id,
            "workspace_id": self.workspace_id,
            "surface_id": self.surface_id,
            "timestamp": self.timestamp,
            "label": self.label,
        }


class FocusHistory:
    """Tracks focus history across panes and workspaces.

    Maintains a bounded stack of recent focus events. The most recent
    entry is at the end of the list.

    Usage::

        history = FocusHistory(max_size=10)
        history.push(pane_id="1", workspace_id="ws-1")
        history.push(pane_id="2", workspace_id="ws-1")
        recent = history.recent(5)
        prev = history.previous()
    """

    def __init__(self, max_size=50):
        self._entries = []
        self._max_size = max_size
        self._lock = threading.Lock()

    def push(self, pane_id=None, workspace_id=None, surface_id=None, label=None):
        """Record a focus event.

        Deduplicates: if the most recent entry has the same pane_id,
        it is replaced instead of duplicated.

        Args:
            pane_id: The pane that received focus.
            workspace_id: The workspace context.
            surface_id: The surface context.
            label: Optional human-readable label.
        """
        entry = FocusEntry(pane_id, workspace_id, surface_id, label)
        with self._lock:
            # Deduplicate consecutive same-pane focuses
            if self._entries:
                last = self._entries[-1]
                if (last.pane_id == entry.pane_id and
                        last.workspace_id == entry.workspace_id):
                    self._entries[-1] = entry
                    return
            self._entries.append(entry)
            # Trim to max size
            if len(self._entries) > self._max_size:
                self._entries = self._entries[-self._max_size:]

    def recent(self, n=5):
        """Return the last N focus entries, most recent first.

        Args:
            n: Number of entries to return.

        Returns:
            List of FocusEntry dicts.
        """
        with self._lock:
            entries = list(self._entries[-n:])
        entries.reverse()
        return [e.to_dict() for e in entries]

    def previous(self, current_pane_id=None):
        """Return the most recent focus entry that is NOT current_pane_id.

        Useful for "last-pane" navigation (tmux-style last-pane).

        Returns:
            FocusEntry dict or None.
        """
        with self._lock:
            for entry in reversed(self._entries):
                if entry.pane_id != current_pane_id:
                    return entry.to_dict()
        return None

    def clear(self):
        """Clear all focus history."""
        with self._lock:
            self._entries.clear()

    def count(self):
        """Return the number of entries in the history."""
        with self._lock:
            return len(self._entries)
