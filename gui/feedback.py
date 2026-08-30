"""GitHub Issues feedback integration for lmux."""
import dataclasses
import os
import platform
import shutil
import subprocess
import sys
import urllib.parse

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk
except ImportError:
    Gtk = None

GITHUB_REPO = "dimona/lmux"
ISSUE_URL_TEMPLATE = (
    "https://github.com/{repo}/issues/new"
    "?title={title}&body={body}&labels={labels}"
)
CATEGORY_LABELS = {
    "bug": "bug",
    "feature": "enhancement",
    "question": "question",
    "other": "feedback",
}
SYSTEM_INFO_CACHE = {}


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------

@dataclasses.dataclass
class FeedbackReport:
    """Structured feedback report for GitHub Issues."""
    title: str
    body: str
    labels: list
    category: str

    def to_github_issue(self):
        """Format as GitHub API issue body dict."""
        return {
            "title": self.title,
            "body": self.body,
            "labels": self.labels,
        }


# ------------------------------------------------------------------
# Bug report template
# ------------------------------------------------------------------

class BugReportTemplate:
    """Pre-filled markdown template for bug reports."""

    @staticmethod
    def build(description="", sys_info=None):
        sys_block = ""
        if sys_info:
            parts = [
                f"- OS: {sys_info.get('os', 'unknown')}",
                f"- Kernel: {sys_info.get('kernel', 'unknown')}",
                f"- lmux version: {sys_info.get('lmux_version', 'unknown')}",
                f"- Python: {sys_info.get('python', 'unknown')}",
                f"- GTK: {sys_info.get('gtk', 'unknown')}",
                f"- Display: {sys_info.get('display', 'unknown')}",
                f"- GPU: {sys_info.get('gpu', 'unavailable')}",
            ]
            sys_block = "\n".join(parts)

        return f"""## Bug Description

{description or "_Describe the bug._"}

## Steps to Reproduce
1.
2.
3.

## Expected Behavior

## Actual Behavior

## System Info
{sys_block or "_System info not collected._"}
"""


# ------------------------------------------------------------------
# Collector
# ------------------------------------------------------------------

class FeedbackCollector:
    """Collects system info and submits feedback via gh CLI or browser."""

    @staticmethod
    def is_gh_available():
        """Check if gh CLI is installed."""
        return shutil.which("gh") is not None

    @staticmethod
    def get_system_info():
        """Collect system information.  Never crashes — returns best-effort dict."""
        if SYSTEM_INFO_CACHE:
            return SYSTEM_INFO_CACHE

        info = {
            "os": f"{platform.system()} {platform.release()}",
            "kernel": platform.release(),
            "python": platform.python_version(),
            "gtk": "unknown",
            "display": "unknown",
            "lmux_version": "unknown",
            "gpu": "unavailable",
        }

        # GTK version
        try:
            if Gtk is not None:
                info["gtk"] = f"{Gtk.MAJOR_VERSION}.{Gtk.MINOR_VERSION}.{Gtk.MICRO_VERSION}"
        except Exception:
            pass

        # Display server
        try:
            if os.environ.get("WAYLAND_DISPLAY"):
                info["display"] = "Wayland"
            elif os.environ.get("DISPLAY"):
                info["display"] = "X11"
            else:
                info["display"] = os.environ.get("XDG_SESSION_TYPE", "unknown")
        except Exception:
            pass

        # lmux version from package.json
        try:
            pkg = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                os.pardir,
                "package.json",
            )
            if os.path.isfile(pkg):
                import json
                with open(pkg) as f:
                    info["lmux_version"] = json.load(f).get("version", "unknown")
        except Exception:
            pass

        # GPU info via glxinfo (optional, slow)
        try:
            if shutil.which("glxinfo"):
                out = subprocess.check_output(
                    ["glxinfo"], stderr=subprocess.DEVNULL, timeout=2
                ).decode(errors="replace")
                for line in out.splitlines():
                    if "OpenGL renderer string:" in line:
                        info["gpu"] = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass

        SYSTEM_INFO_CACHE.update(info)
        return info

    @classmethod
    def generate_report(cls, category, title, description, include_system=True):
        """Build a FeedbackReport with optional system info appended."""
        sys_info = cls.get_system_info() if include_system else None

        if category == "bug":
            body = BugReportTemplate.build(description, sys_info)
        else:
            body = description
            if sys_info:
                info_lines = "\n".join(f"- {k}: {v}" for k, v in sys_info.items())
                body += f"\n\n---\n**System Info**\n{info_lines}"

        label = CATEGORY_LABELS.get(category, "feedback")
        return FeedbackReport(title=title, body=body, labels=[label], category=category)

    @classmethod
    def submit_report(cls, report):
        """Submit via `gh issue create`.  Returns issue URL or None on failure."""
        if not cls.is_gh_available():
            return None
        issue = report.to_github_issue()
        cmd = [
            "gh", "issue", "create",
            "--repo", GITHUB_REPO,
            "--title", issue["title"],
            "--body", issue["body"],
        ]
        for label in issue["labels"]:
            cmd += ["--label", label]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
        return None

    @classmethod
    def open_in_browser(cls, report):
        """Open GitHub new-issue page with pre-filled fields."""
        issue = report.to_github_issue()
        params = urllib.parse.urlencode({
            "title": issue["title"],
            "body": issue["body"],
            "labels": ",".join(issue["labels"]),
        })
        url = ISSUE_URL_TEMPLATE.format(repo=GITHUB_REPO, **urllib.parse.parse_qs(params, keep_blank_values=True))
        # Simpler: just build URL manually
        url = (
            f"https://github.com/{GITHUB_REPO}/issues/new"
            f"?{params}"
        )
        import webbrowser
        webbrowser.open(url)


# ------------------------------------------------------------------
# GTK Dialog
# ------------------------------------------------------------------

if Gtk is not None:

    class FeedbackDialog(Gtk.Dialog):
        """Modal dialog for submitting lmux feedback to GitHub Issues."""

        def __init__(self, parent=None):
            super().__init__(
                title="Submit Feedback",
                transient_for=parent,
                modal=True,
            )
            self.set_default_size(520, 480)
            self.add_buttons(
                "Cancel", Gtk.ResponseType.CANCEL,
                "Open in Browser", Gtk.ResponseType.NO,
                "Submit", Gtk.ResponseType.OK,
            )

            content = self.get_content_area()
            content.set_spacing(8)
            content.set_border_width(12)

            # Category selector
            cat_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            cat_box.pack_start(Gtk.Label(label="Category:"), False, False, 0)
            self._category = Gtk.ComboBoxText()
            for key, label in [
                ("bug", "Bug Report"),
                ("feature", "Feature Request"),
                ("question", "Question"),
                ("other", "Other"),
            ]:
                self._category.append(key, label)
            self._category.set_active_id("bug")
            cat_box.pack_start(self._category, True, True, 0)
            content.add(cat_box)

            # Title
            content.add(Gtk.Label(label="Title", xalign=0))
            self._title = Gtk.Entry()
            self._title.set_placeholder_text("Brief summary of the issue…")
            content.add(self._title)

            # Description
            content.add(Gtk.Label(label="Description", xalign=0))
            scroll = Gtk.ScrolledWindow()
            scroll.set_min_content_height(160)
            self._desc = Gtk.TextView()
            self._desc.set_wrap_mode(Gtk.WrapMode.WORD)
            scroll.add(self._desc)
            content.add(scroll)

            # System info checkbox
            self._sysinfo_cb = Gtk.CheckButton(label="Include system info")
            self._sysinfo_cb.set_active(True)
            content.add(self._sysinfo_cb)

            # Status label
            self._status = Gtk.Label()
            self._status.set_xalign(0)
            self._status.set_selectable(True)
            content.add(self._status)

            self.show_all()

        def _get_text(self):
            buf = self._desc.get_buffer()
            start, end = buf.get_bounds()
            return buf.get_text(start, end, True)

        def run(self):
            """Show dialog and return (action, FeedbackReport | None)."""
            while True:
                resp = Gtk.Dialog.run(self)
                if resp == Gtk.ResponseType.DELETE_EVENT or resp == Gtk.ResponseType.CANCEL:
                    self.destroy()
                    return "cancel", None

                title = self._title.get_text().strip()
                desc = self._get_text().strip()
                category = self._category.get_active_id()
                if not title:
                    self._status.set_text("⚠ Title is required.")
                    continue

                include_sys = self._sysinfo_cb.get_active()
                report = FeedbackCollector.generate_report(
                    category, title, desc, include_sys
                )

                if resp == Gtk.ResponseType.NO:
                    FeedbackCollector.open_in_browser(report)
                    self._status.set_text("Opened in browser.")
                    self.destroy()
                    return "browser", report

                # Submit via gh
                url = FeedbackCollector.submit_report(report)
                if url:
                    self._status.set_text(f"✓ {url}")
                    self.destroy()
                    return "submitted", report
                else:
                    self._status.set_text(
                        "⚠ gh CLI not found. Use 'Open in Browser' instead."
                    )
                    continue
