"""SSH workspace dialog and launcher for lmux GUI.

Provides a GTK3 dialog for entering SSH connection details and opening
a remote workspace backed by a multiplexed SSH session.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gtk, Vte, GLib

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ssh_client import SSHClient, SSHHost, get_ssh_client, _SOCKET_DIR


# ── workspace dialog ─────────────────────────────────────────

class SSHWorkspaceDialog(Gtk.Dialog):
    """Dialog for collecting SSH connection details.

    Returns the connection parameters on Accept via ``self.result``.
    """

    def __init__(self, parent=None):
        super().__init__(
            title="Open SSH Workspace",
            transient_for=parent,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        self.set_default_size(460, 340)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.add_button("Connect", Gtk.ResponseType.ACCEPT)
        self.result = None  # set on ACCEPT

        content = self.get_content_area()
        content.set_spacing(8)
        content.set_border_width(12)

        grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        content.pack_start(grid, True, True, 0)

        # Host
        grid.attach(Gtk.Label(label="Host:", xalign=0), 0, 0, 1, 1)
        self._host = Gtk.Entry()
        self._host.set_placeholder_text("e.g. 192.168.1.100 or myserver.com")
        grid.attach(self._host, 1, 0, 1, 1)

        # Port
        grid.attach(Gtk.Label(label="Port:", xalign=0), 0, 1, 1, 1)
        self._port = Gtk.SpinButton(
            adjustment=Gtk.Adjustment(value=22, lower=1, upper=65535, step_inc=1)
        )
        grid.attach(self._port, 1, 1, 1, 1)

        # User
        grid.attach(Gtk.Label(label="User:", xalign=0), 0, 2, 1, 1)
        self._user = Gtk.Entry()
        self._user.set_placeholder_text("remote username")
        grid.attach(self._user, 1, 2, 1, 1)

        # Key file
        grid.attach(Gtk.Label(label="Key File:", xalign=0), 0, 3, 1, 1)
        key_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._key_file = Gtk.Entry()
        self._key_file.set_placeholder_text("~/.ssh/id_rsa (optional)")
        key_box.pack_start(self._key_file, True, True, 0)
        browse_btn = Gtk.Button(label="…")
        browse_btn.set_size_request(32, -1)
        browse_btn.connect("clicked", self._on_browse_key)
        key_box.pack_end(browse_btn, False, False, 0)
        grid.attach(key_box, 1, 3, 1, 1)

        # Remote directory
        grid.attach(Gtk.Label(label="Remote Dir:", xalign=0), 0, 4, 1, 1)
        self._remote_dir = Gtk.Entry()
        self._remote_dir.set_placeholder_text("~ (optional)")
        grid.attach(self._remote_dir, 1, 4, 1, 1)

        # Password (hidden field, toggled by checkbox)
        self._use_password = Gtk.CheckButton(label="Use password authentication")
        grid.attach(self._use_password, 0, 5, 2, 1)
        self._password = Gtk.Entry()
        self._password.set_visibility(False)
        self._password.set_placeholder_text("password")
        self._password.set_sensitive(False)
        grid.attach(self._password, 1, 6, 1, 1)
        self._use_password.connect("toggled", self._on_pw_toggle)

        # Agent forwarding
        self._agent_fwd = Gtk.CheckButton(label="Enable SSH agent forwarding")
        grid.attach(self._agent_fwd, 0, 7, 2, 1)

        # Status label
        self._status = Gtk.Label(label="", xalign=0)
        self._status.set_opacity(0.0)
        content.pack_end(self._status, False, False, 0)

        self.show_all()

    # ── callbacks ────────────────────────────────────────────

    def _on_browse_key(self, _btn):
        dlg = Gtk.FileChooserDialog(
            title="Select SSH Key",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("Open", Gtk.ResponseType.ACCEPT)
        home = os.path.expanduser("~/.ssh")
        if os.path.isdir(home):
            dlg.set_current_folder(home)
        resp = dlg.run()
        if resp == Gtk.ResponseType.ACCEPT:
            self._key_file.set_text(dlg.get_filename())
        dlg.destroy()

    def _on_pw_toggle(self, check):
        self._password.set_sensitive(check.get_active())

    # ── public ───────────────────────────────────────────────

    def get_host(self) -> SSHHost:
        """Build an SSHHost from the dialog fields."""
        return SSHHost(
            host=self._host.get_text().strip(),
            port=int(self._port.get_value()),
            user=self._user.get_text().strip(),
            key_file=self._key_file.get_text().strip(),
            agent_forwarding=self._agent_fwd.get_active(),
        )

    def get_password(self) -> str:
        if self._use_password.get_active():
            return self._password.get_text()
        return ""

    def get_remote_dir(self) -> str:
        return self._remote_dir.get_text().strip()

    def show_status(self, msg: str, isError: bool = True):
        self._status.set_text(msg)
        self._status.set_opacity(1.0)
        if isError:
            self._status.get_style_context().add_class("error")
        else:
            self._status.get_style_context().remove_class("error")


# ── SSH terminal widget ──────────────────────────────────────

class SSHTerminalWidget(Gtk.Box):
    """A VTE terminal connected to a remote shell via SSH session."""

    def __init__(self, session_id: str, host: SSHHost, title: str = ""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._session_id = session_id
        self._host = host
        self._client = get_ssh_client()

        # Title bar
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title_box.set_margin_start(8)
        title_box.set_margin_end(8)
        title_box.set_margin_top(4)
        title_box.set_margin_bottom(4)
        self.pack_start(title_box, False, False, 0)

        icon = Gtk.Label(label="\u26a1")  # lightning bolt for SSH
        title_box.pack_start(icon, False, False, 0)

        label = title or f"SSH: {host.ssh_uri}"
        self._title_label = Gtk.Label(label=label)
        self._title_label.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        title_box.pack_start(self._title_label, True, True, 0)

        disconnect_btn = Gtk.Button(label="\u2715")
        disconnect_btn.set_relief(Gtk.ReliefStyle.NONE)
        disconnect_btn.set_tooltip_text("Disconnect")
        disconnect_btn.connect("clicked", self._on_disconnect)
        title_box.pack_end(disconnect_btn, False, False, 0)

        # VTE terminal
        self._vte = Vte.Terminal()
        self._vte.set_scrollbackLines(10000)
        self._vte.set_cursorBlinkMode(Vte.CursorBlinkMode.ON)

        scroll = Gtk.ScrolledWindow()
        scroll.add(self._vte)
        self.pack_start(scroll, True, True, 0)

        # Fork shell through the SSH multiplex socket
        sp = self._client._socket_path(session_id)
        argv = [
            "ssh",
            "-o", f"ControlPath={sp}",
            "-o", "ControlMaster=no",
            "-o", "StrictHostKeyChecking=accept-new",
        ]
        if host.port != 22:
            argv += ["-p", str(host.port)]
        argv.append(host.ssh_uri)

        envp = [f"{k}={v}" for k, v in os.environ.items()
                if k.isupper() and k not in ("DISPLAY",)]

        try:
            self._vte.spawn_async(
                Vte.PtyFlags.DEFAULT,
                None,           # working dir
                argv,
                envp,
                GLib.SpawnFlags.DO_NOT_REAP_CHILD,
                None,           # child setup
                -1,             # default timeout
                None,           # cancellable
                self._on_vte_ready,
                None,
            )
        except GLib.Error as exc:
            self._title_label.set_text(f"SSH Error: {exc}")

        self.show_all()

    def _on_vte_ready(self, _vte, _pid, _error):
        pass  # terminal is ready

    def _on_disconnect(self, _btn):
        self._client.disconnect(self._session_id)
        parent = self.get_parent()
        if parent:
            parent.remove(self)

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def title(self) -> str:
        return self._title_label.get_text()

    @title.setter
    def title(self, value: str):
        self._title_label.set_text(value)


# ── top-level convenience ────────────────────────────────────

def open_ssh_workspace(parent=None) -> SSHTerminalWidget or None:
    """Show the dialog and return an SSHTerminalWidget on success, or None."""
    dlg = SSHWorkspaceDialog(parent)
    response = dlg.run()

    if response != Gtk.ResponseType.ACCEPT:
        dlg.destroy()
        return None

    host = dlg.get_host()
    password = dlg.get_password()
    remote_dir = dlg.get_remote_dir()
    dlg.destroy()

    if not host.host:
        return None

    client = get_ssh_client()
    try:
        session_id = client.connect(host, password=password, remote_dir=remote_dir)
    except ConnectionError as exc:
        err_dlg = Gtk.MessageDialog(
            transient_for=parent,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="SSH Connection Failed",
        )
        err_dlg.format_secondary_text(str(exc))
        err_dlg.run()
        err_dlg.destroy()
        return None

    label = f"SSH: {host.ssh_uri}"
    if remote_dir:
        label += f" ({remote_dir})"
    return SSHTerminalWidget(session_id, host, title=label)
