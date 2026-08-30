"""Sparkle-like auto-updater for lmux on Linux.

Parses a Sparkle AppCast XML feed, downloads and verifies updates using
Ed25519 signatures (or SHA-256 fallback), and presents a GTK dialog for
install.  No external dependencies beyond stdlib + PyNaCl (optional for
Ed25519; SHA-256 fallback is stdlib-only).
"""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, GObject

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────

CURRENT_VERSION: str = "1.0.0"  # keep in sync with include/lmux.h
AUTO_UPDATE_INTERVAL: int = 86400  # seconds (24 h)

_CONFIG_DIR = Path.home() / ".lmux"
_FEED_URL_PATH = _CONFIG_DIR / "update_feed_url"
_DEFAULT_FEED = (
    "https://github.com/dream-pixels-forge/lmux/releases.atom"
)

# Sparkle XML namespace
_NS = {"sparkle": "http://www.andymatuschak.org/xml-namespaces/sparkle"}

# Where downloaded archives are staged before install
_STAGING_DIR = _CONFIG_DIR / "updates"


# ── Version helpers ──────────────────────────────────────────

def _parse_version(v: str) -> tuple[int, ...]:
    """Parse ``'1.2.3'`` or ``'v1.2.3-beta'`` into ``(1, 2, 3)``."""
    try:
        v = v.strip().lstrip("v").split("-", 1)[0]
        return tuple(int(p) for p in v.split("."))
    except (ValueError, AttributeError):
        return (0, 0, 0)


# ── Data model ───────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class UpdateInfo:
    """One release entry parsed from an AppCast feed."""
    version: str
    release_notes: str
    download_url: str
    signature: str  # Ed25519 hex or SHA-256 hex
    size: int
    pub_date: str
    min_system_version: str


# ── AppCast parser ───────────────────────────────────────────

class AppCastFeed:
    """Parse a Sparkle AppCast XML feed into a list of ``UpdateInfo``."""

    @staticmethod
    def parse(xml_bytes: bytes) -> list[UpdateInfo]:
        """Parse raw XML bytes and return items newest-first."""
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            log.warning("AppCast XML parse error: %s", exc)
            return []

        items: list[UpdateInfo] = []
        for item_el in root.iter("item"):
            title = _text(item_el, "title", "")
            pub_date = _text(item_el, "pubDate", "")
            release_notes = _text_cdata(item_el, "description", "")
            min_sys = _attr(
                item_el.find("sparkle:minimumSystemVersion", _NS),
                "text", "",
            )

            enc = item_el.find("enclosure")
            if enc is None:
                continue

            url = enc.get("url", "")
            length_str = enc.get("length", "0")
            sig = _attr(enc, "{http://www.andymatuschak.org/xml-namespaces/sparkle}edSignature", "")

            try:
                length = int(length_str)
            except ValueError:
                length = 0

            if url:
                items.append(UpdateInfo(
                    version=title.lstrip("v"),
                    release_notes=release_notes,
                    download_url=url,
                    signature=sig,
                    size=length,
                    pub_date=pub_date,
                    min_system_version=min_sys,
                ))
        return items


def _text(el: Optional[ET.Element], tag: str, default: str) -> str:
    child = el.find(tag) if el is not None else None
    return (child.text or "").strip() if child is not None else default


def _text_cdata(el: Optional[ET.Element], tag: str, default: str) -> str:
    child = el.find(tag) if el is not None else None
    if child is None:
        return default
    # ElementTree merges CDATA into .text
    return (child.text or "").strip()


def _attr(el: Optional[ET.Element], attr: str, default: str) -> str:
    if el is None:
        return default
    return el.get(attr, default)


# ── Crypto helpers ───────────────────────────────────────────

def verify_signature(
    download_data: bytes,
    signature: str,
    public_key: Optional[bytes] = None,
) -> bool:
    """Verify *download_data* against *signature*.

    If *public_key* is supplied, try Ed25519 first.  Always fall back
    to SHA-256 hex comparison when Ed25519 is unavailable or fails.
    """
    from gui.updates.verify import verify_ed25519, verify_sha256

    # Try Ed25519 first when a public key is available
    if public_key and signature:
        try:
            raw_sig = bytes.fromhex(signature)
        except ValueError:
            raw_sig = b""
        if raw_sig and verify_ed25519(download_data, raw_sig, public_key):
            return True

    # SHA-256 fallback
    if signature and len(signature) == 64:
        return verify_sha256(download_data, signature)

    # No signature to check against — accept silently but log warning
    log.warning("No verifiable signature provided; update accepted without verification")
    return True


# ── Public key loading ───────────────────────────────────────

def _load_public_key() -> Optional[bytes]:
    """Load Ed25519 public key from ~/.lmux/update.pub if present."""
    key_path = _CONFIG_DIR / "update.pub"
    try:
        return key_path.read_bytes().strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.debug("Cannot read public key: %s", exc)
        return None


# ── Core updater ─────────────────────────────────────────────

class SparkleUpdater:
    """Sparkle-like update engine for lmux."""

    def check_for_updates(
        self,
        feed_url: Optional[str] = None,
        current_version: Optional[str] = None,
    ) -> Optional[UpdateInfo]:
        """Fetch the AppCast feed and return the newest applicable item.

        Returns ``None`` if no update is available or the check fails.
        """
        url = feed_url or self._feed_url()
        cur = _parse_version(current_version or CURRENT_VERSION)

        try:
            req = Request(url, headers={
                "User-Agent": f"lmux/{CURRENT_VERSION}",
                "Accept": "application/atom+xml, application/xml, */*",
            })
            with urlopen(req, timeout=15) as resp:
                xml_data = resp.read()
        except (URLError, OSError) as exc:
            log.debug("Feed fetch failed (%s): %s", url, exc)
            return None

        items = AppCastFeed.parse(xml_data)
        for info in items:
            ver = _parse_version(info.version)
            if ver > cur:
                return info
        return None

    def download_update(
        self,
        url: str,
        dest_path: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> str:
        """Download *url* to *dest_path* atomically.

        Writes to a temp file first, then renames.  Calls
        ``progress_callback(bytes_downloaded, total_size)`` during the
        transfer when *total_size* is known.

        Returns the final file path on success; raises on failure.
        """
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        req = Request(url, headers={
            "User-Agent": f"lmux/{CURRENT_VERSION}",
        })
        try:
            resp = urlopen(req, timeout=120)
        except (URLError, OSError) as exc:
            raise RuntimeError(f"Download failed: {exc}") from exc

        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        tmp = dest.with_suffix(dest.suffix + ".tmp")

        try:
            with open(tmp, "wb") as fh:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        GLib.idle_add(progress_callback, downloaded, total)

            # Atomic rename
            tmp.replace(dest)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

        return str(dest)

    def apply_update(self, archive_path: str, install_dir: str) -> bool:
        """Extract *archive_path* into *install_dir*.

        If the extracted tree contains an ``install.sh`` at its root,
        it is executed (with *install_dir* as CWD).  Returns ``True``
        on success.
        """
        archive = Path(archive_path)
        install = Path(install_dir)

        if not archive.exists():
            log.error("Archive not found: %s", archive)
            return False

        # Determine if it's a tarball or a plain zip
        if tarfile.is_tarfile(str(archive)):
            return self._apply_tarball(archive, install)
        else:
            return self._apply_raw(archive, install)

    # ── private helpers ──────────────────────────────────────

    def _apply_tarball(self, archive: Path, install: Path) -> bool:
        tmp_extract = install.parent / f".extract_{archive.stem}"
        try:
            with tarfile.open(str(archive), "r:*") as tf:
                # Security: reject paths that escape the target
                for member in tf.getmembers():
                    member_path = os.path.join(tmp_extract, member.name)
                    if not os.path.abspath(member_path).startswith(
                        os.path.abspath(str(tmp_extract))
                    ):
                        log.error("Refusing to extract %s — path escape", member.name)
                        return False
                tf.extractall(str(tmp_extract))

            # Check for install script
            script = None
            for child in tmp_extract.iterdir():
                if child.is_dir():
                    candidate = child / "install.sh"
                    if candidate.exists():
                        script = candidate
                        # Move contents from the top-level dir to install dir
                        for item in child.iterdir():
                            dest = install / item.name
                            if dest.exists():
                                dest.unlink() if dest.is_file() else shutil.rmtree(dest)
                            shutil.move(str(item), str(dest))
                        break
                elif child.name == "install.sh":
                    script = child

            if script:
                result = subprocess.run(
                    ["bash", str(script)],
                    cwd=str(install),
                    timeout=120,
                    capture_output=True,
                )
                if result.returncode != 0:
                    log.error(
                        "install.sh failed (rc=%d): %s",
                        result.returncode,
                        result.stderr.decode(errors="replace"),
                    )
                    return False
            else:
                # No install script — copy extracted contents directly
                for child in tmp_extract.iterdir():
                    dest = install / child.name
                    if dest.exists():
                        dest.unlink() if dest.is_file() else shutil.rmtree(dest)
                    shutil.move(str(child), str(dest))

            return True
        except Exception as exc:
            log.error("Apply update failed: %s", exc)
            return False
        finally:
            shutil.rmtree(str(tmp_extract), ignore_errors=True)

    def _apply_raw(self, archive: Path, install: Path) -> bool:
        """Fallback: just copy the archive into install dir."""
        dest = install / archive.name
        shutil.copy2(str(archive), str(dest))
        return True

    @staticmethod
    def _feed_url() -> str:
        """Read feed URL from config, falling back to default."""
        try:
            url = _FEED_URL_PATH.read_text().strip()
            if url:
                return url
        except FileNotFoundError:
            pass
        return _DEFAULT_FEED


# ── GTK Update Dialog ────────────────────────────────────────

class UpdateDialog(Gtk.Dialog):
    """Dialog showing release notes, download progress, and install button."""

    def __init__(
        self,
        parent: Gtk.Window,
        update_info: UpdateInfo,
        updater: SparkleUpdater,
    ):
        super().__init__(
            title=f"Update Available — lmux {update_info.version}",
            transient_for=parent,
            flags=Gtk.DialogFlags.MODAL,
        )
        self._info = update_info
        self._updater = updater
        self._archive_path: Optional[str] = None

        self.set_default_size(520, 420)
        self.add_button("Skip This Version", Gtk.ResponseType.REJECT)
        self.add_button("Install Update", Gtk.ResponseType.ACCEPT)

        box = self.get_content_area()
        box.set_spacing(12)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        # Header
        header = Gtk.Label()
        header.set_markup(
            f"<b><big>lmux {update_info.version}</big></b>\n"
            f"<small>Released {update_info.pub_date}</small>"
        )
        header.set_use_markup(True)
        header.set_xalign(0)
        box.pack_start(header, False, False, 0)

        # Release notes
        notes_label = Gtk.Label()
        notes_label.set_markup(_html_to_pango(update_info.release_notes or "No release notes."))
        notes_label.set_use_markup(True)
        notes_label.set_xalign(0)
        notes_label.set_line_wrap(True)
        notes_label.set_selectable(True)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(200)
        scrolled.add(notes_label)
        box.pack_start(scrolled, True, True, 0)

        # Size info
        size_mb = update_info.size / (1024 * 1024) if update_info.size else 0
        self._size_label = Gtk.Label(
            label=f"Download size: {size_mb:.1f} MB" if size_mb else ""
        )
        self._size_label.set_xalign(0)
        box.pack_start(self._size_label, False, False, 0)

        # Progress bar (hidden initially)
        self._progress = Gtk.ProgressBar()
        self._progress.set_show_text(True)
        self._progress.set_visible(False)
        box.pack_start(self._progress, False, False, 0)

        # Status label
        self._status = Gtk.Label(label="")
        self._status.set_xalign(0)
        self._status.set_visible(False)
        box.pack_start(self._status, False, False, 0)

        self.show_all()
        # Hide progress until download starts
        self._progress.set_visible(False)
        self._status.set_visible(False)

    def do_response(self, response_id: int) -> None:
        if response_id == Gtk.ResponseType.ACCEPT:
            self._start_download()
        else:
            self.destroy()

    def _start_download(self) -> None:
        self._progress.set_visible(True)
        self._status.set_visible(True)
        self._status.set_text("Downloading\u2026")

        _STAGING_DIR.mkdir(parents=True, exist_ok=True)
        dest = str(_STAGING_DIR / f"lmux-{self._info.version}.tar.gz")

        def _progress(downloaded: int, total: int) -> None:
            if total > 0:
                frac = downloaded / total
                self._progress.set_fraction(frac)
                self._progress.set_text(f"{downloaded / 1048576:.1f} / {total / 1048576:.1f} MB")
            else:
                self._progress.pulse()
                self._progress.set_text(f"{downloaded / 1048576:.1f} MB")

        def _run():
            try:
                path = self._updater.download_update(
                    self._info.download_url, dest, _progress,
                )
                # Verify signature
                pub_key = _load_public_key()
                with open(path, "rb") as f:
                    data = f.read()
                if not verify_signature(data, self._info.signature, pub_key):
                    GLib.idle_add(self._on_verify_fail)
                    return
                self._archive_path = path
                GLib.idle_add(self._on_download_complete)
            except Exception as exc:
                GLib.idle_add(self._on_error, str(exc))

        threading.Thread(target=_run, daemon=True).start()

    def _on_download_complete(self) -> None:
        self._progress.set_fraction(1.0)
        self._progress.set_text("Ready to install")
        self._status.set_text("Download complete. Click Install to proceed.")
        # Re-enable install button
        for btn in self.get_action_area().get_children():
            if btn.get_label() == "Skip This Version":
                btn.set_sensitive(True)

    def _on_verify_fail(self) -> None:
        self._progress.set_visible(False)
        self._status.set_text("Signature verification failed. Update rejected.")
        self._status.set_visible(True)

    def _on_error(self, msg: str) -> None:
        self._progress.set_visible(False)
        self._status.set_text(f"Error: {msg}")
        self._status.set_visible(True)


def _html_to_pango(text: str) -> str:
    """Minimal HTML-to-Pango markup conversion for release notes."""
    import html
    text = html.unescape(text)
    # Wrap in a small font for readability
    return f"<small>{text}</small>"


# ── Background update checker ────────────────────────────────

class UpdateChecker(GObject.Object):
    """Periodic background checker that emits ``update-available``.

    GObject signal signature:
        update-available(str version, str url)
    """

    __gsignals__ = {
        "update-available": (
            GObject.SignalFlags.RUN_LAST,
            None,
            (str, str),
        ),
    }

    AUTO_UPDATE_INTERVAL: int = AUTO_UPDATE_INTERVAL

    def __init__(self, feed_url: Optional[str] = None):
        super().__init__()
        self._updater = SparkleUpdater()
        self._feed_url = feed_url
        self._last_check: float = 0.0
        self._latest: Optional[UpdateInfo] = None
        self._timer_id: Optional[int] = None

    def start(self) -> None:
        """Begin periodic update checks (every 24 h + immediate first check)."""
        self.check_now()
        self._timer_id = GLib.timeout_add_seconds(
            self.AUTO_UPDATE_INTERVAL, self._periodic_tick,
        )

    def stop(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def check_now(self) -> None:
        threading.Thread(target=self._do_check, daemon=True).start()

    @property
    def latest(self) -> Optional[UpdateInfo]:
        return self._latest

    # ── internals ────────────────────────────────────────────

    def _periodic_tick(self) -> bool:
        self.check_now()
        return True  # keep timer alive

    def _do_check(self) -> None:
        info = self._updater.check_for_updates(self._feed_url)
        if info:
            self._latest = info
            GLib.idle_add(
                self.emit, "update-available", info.version, info.download_url,
            )


# ── Integration helper ───────────────────────────────────────

def add_update_notification(window: Gtk.Window, status_bar=None) -> UpdateChecker:
    """Wire periodic update checks into *window*.

    Creates an ``UpdateChecker``, starts it, and shows a notification
    bar inside *window* when a new version is found.  Returns the
    checker so callers can stop it or inspect ``.latest``.
    """
    checker = UpdateChecker()

    def _on_update(_obj, version: str, url: str) -> None:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.set_margin_start(8)
        bar.set_margin_end(8)
        bar.set_margin_top(4)
        bar.set_margin_bottom(4)

        css = b"""
        .update-bar { background: #1a5c1a; color: #fff; font-weight: bold; }
        .update-btn { background: #2d8c2d; color: #fff; padding: 2px 8px; }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gtk.Window.get_default_screen() if hasattr(Gtk, "Window") else None,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        ) if False else None  # avoid screen warning — use add_provider_for_screen
        # Apply CSS to the bar
        style_ctx = bar.get_style_context()
        style_ctx.add_class("update-bar")

        lbl = Gtk.Label(label=f"Update available: lmux {version}")
        lbl.set_xalign(0)
        bar.pack_start(lbl, True, True, 0)

        def _on_install_clicked(_btn):
            updater = SparkleUpdater()
            info = checker.latest
            if info:
                dlg = UpdateDialog(window, info, updater)
                dlg.run()
                dlg.destroy()

        install_btn = Gtk.Button(label="Install")
        install_btn.get_style_context().add_class("update-btn")
        install_btn.connect("clicked", _on_install_clicked)
        bar.pack_end(install_btn, False, False, 0)

        dismiss_btn = Gtk.Button(label="\u2715")
        dismiss_btn.connect("clicked", lambda _: bar.destroy())
        bar.pack_end(dismiss_btn, False, False, 0)

        outer_vbox = window.get_children()[0] if window.get_children() else None
        if isinstance(outer_vbox, Gtk.Box):
            outer_vbox.pack_start(bar, False, False, 0)
            for i, child in enumerate(outer_vbox.get_children()):
                if isinstance(child, Gtk.MenuBar):
                    outer_vbox.reorder_child(bar, i + 1)
                    break
        window.show_all()

    checker.connect("update-available", _on_update)
    checker.start()
    return checker


# ── Standalone test ──────────────────────────────────────────

if __name__ == "__main__":
    updater = SparkleUpdater()
    print(f"Current version: {CURRENT_VERSION}")
    print(f"Feed URL: {updater._feed_url()}")
    print("Checking for updates\u2026")
    info = updater.check_for_updates()
    if info:
        print(f"Update available: {info.version}")
        print(f"  URL:      {info.download_url}")
        print(f"  Date:     {info.pub_date}")
        print(f"  Size:     {info.size} bytes")
        print(f"  Notes:    {info.release_notes[:120]}")
    else:
        print("Up to date or feed unreachable.")
