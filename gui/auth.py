"""Authentication system for lmux GUI — PAM integration for Linux.

Provides login/logout, session tracking, and optional PAM-based
authentication for protecting workspace actions.
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, GObject
import os
import sys
import threading
import subprocess
import crypt
import pwd
import spwd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ── Auth backend ────────────────────────────────────────────

class AuthBackend:
    """Authentication backend — verifies credentials against system."""

    def __init__(self):
        self._current_user = None
        self._authenticated = False

    def get_current_user(self):
        """Get the currently logged-in system user."""
        return os.getlogin()

    def get_display_name(self):
        """Get display name for current user."""
        user = self.get_current_user()
        try:
            pw = pwd.getpwnam(user)
            return pw.pw_gecos.split(",")[0] or user
        except KeyError:
            return user

    def authenticate(self, username, password):
        """Verify username/password against system. Returns True if valid.

        Uses crypt() to compare against /etc/shadow.
        Falls back to PAM if shadow access is denied.
        """
        # Method 1: Direct shadow comparison (fast, requires root or same user)
        try:
            shadow = spwd.getspnam(username)
            expected = shadow.sp_pwdp
            if expected and expected not in ("!", "!!", "*", "!!:"):
                if crypt.crypt(password, expected) == expected:
                    self._current_user = username
                    self._authenticated = True
                    return True
            return False
        except (PermissionError, KeyError):
            pass

        # Method 2: PAM via python-pam or systemd-run
        try:
            result = subprocess.run(
                ["pam_authenticate", username, password],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                self._current_user = username
                self._authenticated = True
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Method 3: su -c test
        try:
            result = subprocess.run(
                ["su", "-c", "echo ok", username],
                input=password + "\n",
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and "ok" in result.stdout:
                self._current_user = username
                self._authenticated = True
                return True
        except (subprocess.TimeoutExpired, OSError):
            pass

        return False

    def logout(self):
        """Clear authentication state."""
        self._current_user = None
        self._authenticated = False

    def is_authenticated(self):
        """Check if user is authenticated."""
        return self._authenticated

    def get_user(self):
        """Get authenticated username."""
        return self._current_user


# ── Login dialog ────────────────────────────────────────────

class LoginDialog(Gtk.Dialog):
    """Login dialog for user authentication."""

    def __init__(self, parent=None, backend=None):
        super().__init__(
            title="lmux Login",
            transient_for=parent,
            flags=Gtk.DialogFlags.MODAL,
        )
        self.set_default_size(300, 180)
        self._backend = backend or AuthBackend()

        box = self.get_content_area()
        box.set_spacing(12)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(16)

        # Title
        title = Gtk.Label(label="Authenticate to lmux")
        title.set_markup('<span size="large" weight="bold">Authenticate to lmux</span>')
        box.pack_start(title, False, False, 0)

        # Username
        user_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        user_label = Gtk.Label(label="User:")
        user_label.set_xalign(0)
        user_label.set_size_request(60, -1)
        self._user_entry = Gtk.Entry()
        self._user_entry.set_text(self._backend.get_current_user())
        user_box.pack_start(user_label, False, False, 0)
        user_box.pack_start(self._user_entry, True, True, 0)
        box.pack_start(user_box, False, False, 0)

        # Password
        pass_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        pass_label = Gtk.Label(label="Pass:")
        pass_label.set_xalign(0)
        pass_label.set_size_request(60, -1)
        self._pass_entry = Gtk.Entry()
        self._pass_entry.set_visibility(False)
        self._pass_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self._pass_entry.connect("activate", self._on_login)
        pass_box.pack_start(pass_label, False, False, 0)
        pass_box.pack_start(self._pass_entry, True, True, 0)
        box.pack_start(pass_box, False, False, 0)

        # Error label
        self._error_label = Gtk.Label()
        self._error_label.set_markup('<span color="red">Invalid credentials</span>')
        self._error_label.set_no_show_all(True)
        box.pack_start(self._error_label, False, False, 0)

        # Buttons
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.add_button("Login", Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)

        self.show_all()
        self._error_label.hide()

    def _on_login(self, entry):
        """Handle Enter key in password field."""
        self.response(Gtk.ResponseType.OK)

    def get_credentials(self):
        """Get entered username and password."""
        return self._user_entry.get_text(), self._pass_entry.get_text()

    def do_response(self, response_id):
        if response_id == Gtk.ResponseType.OK:
            username, password = self.get_credentials()
            if self._backend.authenticate(username, password):
                self.destroy()
            else:
                self._error_label.show()
                self._pass_entry.set_text("")
                self._pass_entry.grab_focus()
                # Don't destroy — let user retry
                return True  # keep dialog open
        else:
            self.destroy()


# ── Auth manager (for sidebar integration) ──────────────────

class AuthManager(GObject.GObject):
    """Manages authentication state and UI integration."""

    __gsignals__ = {
        "login": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "logout": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, parent_window=None):
        super().__init__()
        self._backend = AuthBackend()
        self._parent = parent_window
        self._user = None

    @property
    def is_authenticated(self):
        return self._backend.is_authenticated()

    @property
    def current_user(self):
        return self._user

    def login(self):
        """Show login dialog. Returns True if authenticated."""
        dialog = LoginDialog(parent=self._parent, backend=self._backend)
        response = dialog.run()
        if self._backend.is_authenticated():
            self._user = self._backend.get_user()
            self.emit("login", self._user)
            return True
        return False

    def logout(self):
        """Clear authentication."""
        self._backend.logout()
        self._user = None
        self.emit("logout")

    def require_auth(self, action_name="this action"):
        """Check if authenticated, show login dialog if not. Returns True if OK."""
        if self.is_authenticated:
            return True
        # Show warning
        dialog = Gtk.MessageDialog(
            transient_for=self._parent,
            flags=0,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=f"Authentication required for {action_name}",
        )
        dialog.format_secondary_text("Please log in to continue.")
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            return self.login()
        return False


# ── Standalone test ─────────────────────────────────────────

if __name__ == "__main__":
    win = Gtk.Window(title="Auth Test")
    win.set_default_size(300, 100)

    auth = AuthManager(parent_window=win)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_margin_start(16)
    box.set_margin_top(16)

    status = Gtk.Label(label="Not logged in")
    box.pack_start(status, False, False, 0)

    def on_login(mgr, user):
        status.set_text(f"Logged in as: {user}")

    def on_logout(mgr):
        status.set_text("Not logged in")

    auth.connect("login", on_login)
    auth.connect("logout", on_logout)

    btn_login = Gtk.Button(label="Login")
    btn_login.connect("clicked", lambda _: auth.login())
    box.pack_start(btn_login, False, False, 0)

    btn_logout = Gtk.Button(label="Logout")
    btn_logout.connect("clicked", lambda _: auth.logout())
    box.pack_start(btn_logout, False, False, 0)

    win.add(box)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
