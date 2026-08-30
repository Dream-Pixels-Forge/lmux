"""Embedded task manager for lmux — htop/btop in VTE + lightweight popover."""
import os
import signal
import shutil
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import GLib, Gtk, Vte

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_STATE_MAP = {
    "S": "sleeping", "R": "running", "Z": "zombie",
    "D": "disk-sleep", "T": "stopped", "t": "tracing-stop",
    "I": "idle", "X": "dead", "x": "dead", "W": "waking",
}


@dataclass
class ProcessInfo:
    """Snapshot of a single process from /proc."""
    pid: int
    name: str
    cpu_pct: float
    mem_pct: float
    status: str
    user: str

    @classmethod
    def from_pid(cls, pid: int):
        """Read /proc/<pid>/stat and /proc/<pid>/status. Returns None on failure."""
        try:
            stat_text = Path(f"/proc/{pid}/stat").read_text()
            status_text = Path(f"/proc/{pid}/status").read_text()
        except (OSError, PermissionError):
            return None
        lparen, rparen = stat_text.find("("), stat_text.rfind(")")
        if lparen < 0 or rparen < 0:
            return None
        comm = stat_text[lparen + 1:rparen]
        fields = stat_text[rparen + 2:].split()
        if len(fields) < 13:
            return None
        state = _STATE_MAP.get(fields[0], fields[0])
        name, user = comm, ""
        for line in status_text.splitlines():
            if line.startswith("Name:"):
                name = line.split(":", 1)[1].strip() or comm
            elif line.startswith("Uid:"):
                user = line.split(":", 1)[1].split()[0]
                break
        return cls(pid=pid, name=name, cpu_pct=0.0, mem_pct=0.0,
                   status=state, user=user)

    @classmethod
    def list_processes(cls, top_n: int = 10):
        """Read /proc, sort by CPU% descending, return top_n processes."""
        total_ticks, num_cpus = 1, 1
        try:
            with open("/proc/stat") as f:
                for line in f:
                    if line.startswith("cpu "):
                        total_ticks = sum(int(x) for x in line.split()[1:])
                    elif line.startswith("cpu") and line[3:4].isdigit():
                        num_cpus += 1
        except (OSError, PermissionError):
            pass
        total_mem_kb = 1
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        total_mem_kb = int(line.split()[1])
                        break
        except (OSError, PermissionError):
            pass
        procs = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            info = cls.from_pid(int(entry.name))
            if info is None:
                continue
            try:
                rp = entry.joinpath("stat").read_text().rfind(")")
                fields = entry.joinpath("stat").read_text()[rp + 2:].split()
                if len(fields) >= 13:
                    t = int(fields[11]) + int(fields[12])
                    info.cpu_pct = t / total_ticks * num_cpus * 100
            except (OSError, PermissionError, ValueError, IndexError):
                pass
            try:
                for line in entry.joinpath("status").read_text().splitlines():
                    if line.startswith("VmRSS:"):
                        info.mem_pct = int(line.split()[1]) / total_mem_kb * 100
                        break
            except (OSError, PermissionError, ValueError, IndexError):
                pass
            procs.append(info)
        procs.sort(key=lambda p: p.cpu_pct, reverse=True)
        return procs[:top_n]


class TaskManagerWidget(Gtk.Box):
    """VTE terminal running htop (or btop, fallback top)."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._child_pid = None
        # Title bar
        title_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        title_bar.set_margin_start(8)
        title_bar.set_margin_end(4)
        title_bar.set_margin_top(4)
        title_bar.set_margin_bottom(4)
        lbl = Gtk.Label(label="Process Monitor")
        lbl.set_xalign(0)
        lbl.get_style_context().add_class("terminal-title")
        title_bar.pack_start(lbl, True, True, 0)
        refresh_btn = Gtk.Button(label="\u27f3")
        refresh_btn.set_relief(Gtk.ReliefStyle.NONE)
        refresh_btn.set_tooltip_text("Refresh")
        refresh_btn.connect("clicked", lambda _: self.refresh())
        title_bar.pack_end(refresh_btn, False, False, 0)
        close_btn = Gtk.Button(label="\u2715")
        close_btn.set_relief(Gtk.ReliefStyle.NONE)
        close_btn.set_tooltip_text("Close")
        close_btn.connect("clicked", lambda _: self._on_close())
        title_bar.pack_end(close_btn, False, False, 0)
        self.pack_start(title_bar, False, False, 0)
        # VTE terminal — no scrollbars
        self._vte = Vte.Terminal()
        self._vte.set_scrollback_lines(0)
        self.pack_start(self._vte, True, True, 0)
        self._monitor = self._find_monitor()
        self._spawn_monitor()
        self.show_all()

    def _find_monitor(self) -> str:
        for name in ("htop", "btop", "top"):
            if shutil.which(name):
                return name
        return "top"

    def _spawn_monitor(self):
        """Spawn the monitor binary in VTE via spawn_sync."""
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                result = self._vte.spawn_sync(
                    Vte.PtyFlags.DEFAULT, os.getcwd(),
                    [self._monitor], [], GLib.SpawnFlags.SEARCH_PATH,
                    None, None,
                )
            if result and result[0]:
                self._child_pid = result[1]
        except GLib.Error:
            self._child_pid = None

    def refresh(self):
        self._kill_child()
        self._vte.reset(True, True)
        self._spawn_monitor()

    def cleanup(self):
        """SIGTERM → 2 s wait → SIGKILL."""
        self._kill_child(sig=signal.SIGTERM, timeout=2.0)
        self._kill_child(sig=signal.SIGKILL)

    def _kill_child(self, sig=signal.SIGTERM, timeout=0):
        pid = self._child_pid
        if pid is None:
            return
        try:
            os.kill(pid, sig)
        except (OSError, ProcessLookupError):
            return
        if timeout > 0:
            import time
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    os.kill(pid, 0)
                except (OSError, ProcessLookupError):
                    return
                GLib.usleep(50_000)

    def _on_close(self):
        self.cleanup()
        parent = self.get_toplevel()
        if isinstance(parent, Gtk.Window):
            parent.destroy()


class TaskManagerPopover(Gtk.Popover):
    """Lightweight top-10 process list with auto-refresh."""

    def __init__(self):
        super().__init__()
        self._refresh_id = None
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        # Header
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        lbl = Gtk.Label(label="Top Processes")
        lbl.set_xalign(0)
        hdr.pack_start(lbl, True, True, 0)
        htop_btn = Gtk.Button(label="Open in htop")
        htop_btn.set_relief(Gtk.ReliefStyle.NONE)
        htop_btn.connect("clicked", lambda _: self._open_htop())
        hdr.pack_end(htop_btn, False, False, 0)
        refresh_btn = Gtk.Button(label="\u27f3")
        refresh_btn.set_relief(Gtk.ReliefStyle.NONE)
        refresh_btn.connect("clicked", lambda _: self._refresh())
        hdr.pack_end(refresh_btn, False, False, 0)
        box.pack_start(hdr, False, False, 0)
        # TreeView
        self._store = Gtk.ListStore(int, str, float, float, str)
        self._tree = Gtk.TreeView(model=self._store)
        self._tree.set_headers_visible(True)
        for title, cid, w in [("PID", 0, 55), ("Name", 1, 130),
                               ("CPU%", 2, 55), ("MEM%", 3, 55),
                               ("Status", 4, 80)]:
            r = Gtk.CellRendererText()
            c = Gtk.TreeViewColumn(title, r, text=cid)
            c.set_min_width(w)
            if cid in (2, 3):
                r.set_property("xalign", 1.0)
                c.set_cell_data_func(r, self._fmt_pct, cid)
            self._tree.append_column(c)
        self._tree.connect("row-activated", self._on_row_activated)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_size_request(350, 250)
        scroll.add(self._tree)
        box.pack_start(scroll, True, True, 0)
        self.add(box)
        self.show_all()
        self._refresh_id = GLib.timeout_add_seconds(5, self._refresh)
        self.connect("destroy", self._on_destroy)
        self.connect("show", lambda _: self._refresh())

    @staticmethod
    def _fmt_pct(_col, renderer, model, tree_iter, col_id):
        renderer.set_property("text", f"{model[tree_iter][col_id]:.1f}%")

    def _refresh(self):
        self._store.clear()
        for p in ProcessInfo.list_processes(top_n=10):
            self._store.append([p.pid, p.name, p.cpu_pct, p.mem_pct, p.status])
        return True

    def _on_row_activated(self, tree_view, path, _column):
        model = tree_view.get_model()
        it = model.get_iter(path)
        pid, name = model[it][0], model[it][1]
        dialog = Gtk.MessageDialog(
            transient_for=self.get_toplevel(), flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.WARNING, buttons=Gtk.ButtonsType.OK_CANCEL,
            text=f"Send SIGTERM to {name} (PID {pid})?",
        )
        resp = dialog.run()
        dialog.destroy()
        if resp == Gtk.ResponseType.OK:
            try:
                os.kill(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
            GLib.timeout_add(500, self._refresh)

    def _open_htop(self):
        win = Gtk.Window(title="Process Monitor")
        win.set_default_size(800, 500)
        widget = TaskManagerWidget()
        win.add(widget)
        win.connect("destroy", lambda _: widget.cleanup())
        win.show_all()

    def _on_destroy(self, _widget):
        if self._refresh_id is not None:
            GLib.source_remove(self._refresh_id)
            self._refresh_id = None
