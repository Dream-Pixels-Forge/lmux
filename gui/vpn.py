"""Tailscale/WireGuard VPN integration for lmux.

Provides status monitoring, connect/disconnect control, peer listing,
and latency probing -- all via subprocess with no external dependencies.
Falls back to raw WireGuard when Tailscale is absent.
"""
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('GLib', '2.0')
from gi.repository import Gtk, GLib, Gdk

import json
import shutil
import subprocess
import threading
import re
from dataclasses import dataclass, field
from typing import List, Optional

_TIMEOUT = 10  # seconds -- hard cap on every subprocess call


# ================================================================
# Data models
# ================================================================

@dataclass
class TailscalePeer:
    """One Tailscale peer (machine) on the tailnet."""
    hostname: str
    ip: str
    os: str
    online: bool
    tailnet: str


@dataclass
class TailscaleStatus:
    """Snapshot of the local Tailscale state."""
    connected: bool
    hostname: str
    tailnet: str
    ip: str
    peers: List[TailscalePeer] = field(default_factory=list)

    @classmethod
    def from_cli(cls) -> "TailscaleStatus":
        """Run ``tailscale status --json`` and parse the result."""
        try:
            raw = subprocess.check_output(
                ["tailscale", "status", "--json"],
                timeout=_TIMEOUT,
                stderr=subprocess.DEVNULL,
            )
            data = json.loads(raw)
        except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError):
            return cls(connected=False, hostname="", tailnet="", ip="")

        self_node = data.get("Self", {})
        peers_raw = data.get("Peer", {})

        peers: List[TailscalePeer] = []
        for _pid, p in peers_raw.items():
            peers.append(TailscalePeer(
                hostname=p.get("HostName", ""),
                ip=p.get("TailscaleIPs", [""])[0] if p.get("TailscaleIPs") else "",
                os=p.get("OS", ""),
                online=p.get("Online", False),
                tailnet=data.get("CurrentTailnet", {}).get("Name", ""),
            ))

        return cls(
            connected=self_node.get("Online", False),
            hostname=self_node.get("HostName", ""),
            tailnet=data.get("CurrentTailnet", {}).get("Name", ""),
            ip=self_node.get("TailscaleIPs", [""])[0] if self_node.get("TailscaleIPs") else "",
            peers=peers,
        )


# ================================================================
# Backend managers
# ================================================================

class VPNManager:
    """High-level Tailscale control plane.

    Every public method is safe to call even when Tailscale is not
    installed; they return sensible defaults instead of raising.
    """

    @staticmethod
    def is_tailscale_available() -> bool:
        """Return True if ``tailscale`` is on PATH."""
        return shutil.which("tailscale") is not None

    def get_status(self) -> TailscaleStatus:
        """Parse ``tailscale status --json`` into a dataclass."""
        return TailscaleStatus.from_cli()

    def connect(self) -> bool:
        """Run ``tailscale up``. Returns True on success."""
        if not self.is_tailscale_available():
            return False
        try:
            subprocess.check_call(
                ["tailscale", "up"],
                timeout=_TIMEOUT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except (subprocess.SubprocessError, FileNotFoundError):
            return False

    def disconnect(self) -> bool:
        """Run ``tailscale down``. Returns True on success."""
        if not self.is_tailscale_available():
            return False
        try:
            subprocess.check_call(
                ["tailscale", "down"],
                timeout=_TIMEOUT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except (subprocess.SubprocessError, FileNotFoundError):
            return False

    def get_peers(self) -> List[TailscalePeer]:
        """Return the current peer list."""
        return self.get_status().peers

    def ping(self, host: str) -> Optional[float]:
        """Ping a host via Tailscale. Returns latency in ms or None."""
        if not self.is_tailscale_available():
            return None
        try:
            raw = subprocess.check_output(
                ["tailscale", "ping", "-c", "1", host],
                timeout=_TIMEOUT,
                stderr=subprocess.DEVNULL,
            ).decode("utf-8", errors="replace")
            # Parse "8 bytes from 100.x.x.x: seq=0 time=1.234ms"
            match = re.search(r"time=([\d.]+)\s*ms", raw)
            return float(match.group(1)) if match else None
        except (subprocess.SubprocessError, FileNotFoundError):
            return None


class WireGuardFallback:
    """Minimal ``wg show`` parser for machines without Tailscale."""

    @staticmethod
    def is_available() -> bool:
        """Return True if ``wg`` is on PATH."""
        return shutil.which("wg") is not None

    def get_status(self) -> dict:
        """Parse ``wg show`` into a structured dict.

        Returns keys: ``interfaces`` (list), ``transfers`` (list).
        Each interface dict has ``name``, ``public_key``, ``listen_port``,
        ``peers`` (list of dicts with ``public_key``, ``allowed_ips``,
        ``endpoint``, ``latest_handshake``).
        """
        if not self.is_available():
            return {"interfaces": [], "transfers": []}
        try:
            raw = subprocess.check_output(
                ["wg", "show"],
                timeout=_TIMEOUT,
                stderr=subprocess.DEVNULL,
            ).decode("utf-8", errors="replace")
        except (subprocess.SubprocessError, FileNotFoundError):
            return {"interfaces": [], "transfers": []}

        interfaces = []
        transfers = []
        current = None

        for line in raw.splitlines():
            line = line.rstrip()
            # New interface header: "interface: wg0"
            m_iface = re.match(r"^interface:\s*(\S+)", line)
            if m_iface:
                current = {
                    "name": m_iface.group(1),
                    "public_key": "",
                    "listen_port": "",
                    "peers": [],
                }
                interfaces.append(current)
                continue

            if current is None:
                continue

            m_pk = re.match(r"^\s*public key:\s*(\S+)", line)
            if m_pk:
                current["public_key"] = m_pk.group(1)
                continue

            m_lp = re.match(r"^\s*listening port:\s*(\S+)", line)
            if m_lp:
                current["listen_port"] = m_lp.group(1)
                continue

            m_peer = re.match(r"^\s*peer:\s*(\S+)", line)
            if m_peer:
                current["peers"].append({
                    "public_key": m_peer.group(1),
                    "allowed_ips": "",
                    "endpoint": "",
                    "latest_handshake": "",
                })
                continue

            peer = current["peers"][-1] if current["peers"] else None
            if peer is None:
                continue

            m_ai = re.match(r"^\s*allowed ips:\s*(.*)", line)
            if m_ai:
                peer["allowed_ips"] = m_ai.group(1).strip()
                continue

            m_ep = re.match(r"^\s*endpoint:\s*(.*)", line)
            if m_ep:
                peer["endpoint"] = m_ep.group(1).strip()
                continue

            m_hs = re.match(r"^\s*latest handshake:\s*(.*)", line)
            if m_hs:
                peer["latest_handshake"] = m_hs.group(1).strip()
                continue

            m_tf = re.match(r"^\s*transfer:\s*(.*)", line)
            if m_tf:
                transfers.append({
                    "peer_key": peer["public_key"],
                    "detail": m_tf.group(1).strip(),
                })

        return {"interfaces": interfaces, "transfers": transfers}


# ================================================================
# GTK3 Panel
# ================================================================

class VPNPanel(Gtk.Box):
    """Sidebar panel showing Tailscale status and peer list.

    Designed to be embedded in the lmux sidebar via
    ``sidebar.register_extension()`` or packed directly.
    """

    _REFRESH_INTERVAL = 30  # seconds

    def __init__(self, parent_window=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._mgr = VPNManager()
        self._wg = WireGuardFallback()
        self._parent = parent_window
        self._timer_id = None
        self._current_status: Optional[TailscaleStatus] = None

        self._build_ui()
        self._refresh()

    # -- UI construction -----------------------------------------

    def _build_ui(self):
        # -- Header row: status dot + hostname + IP --
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hdr.set_margin_start(8)
        hdr.set_margin_end(8)
        hdr.set_margin_top(6)
        hdr.set_margin_bottom(4)

        self._dot = Gtk.Label(label="\u25cf")
        self._dot.set_markup('<span foreground="#888888">\u25cf</span>')
        hdr.pack_start(self._dot, False, False, 0)

        self._host_label = Gtk.Label(label="VPN")
        self._host_label.set_xalign(0)
        self._host_label.get_style_context().add_class("workspace-header")
        hdr.pack_start(self._host_label, True, True, 0)

        self._ip_label = Gtk.Label(label="")
        self._ip_label.set_xalign(1)
        self._ip_label.set_opacity(0.6)
        hdr.pack_start(self._ip_label, False, False, 0)

        self.pack_start(hdr, False, False, 0)

        # -- Action buttons --
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        btn_box.set_margin_start(8)
        btn_box.set_margin_end(8)
        btn_box.set_margin_bottom(4)

        self._toggle_btn = Gtk.Button(label="Connect")
        self._toggle_btn.set_relief(Gtk.ReliefStyle.NONE)
        self._toggle_btn.connect("clicked", self._on_toggle)
        btn_box.pack_start(self._toggle_btn, False, False, 0)

        self._copy_btn = Gtk.Button(label="Copy IP")
        self._copy_btn.set_relief(Gtk.ReliefStyle.NONE)
        self._copy_btn.set_tooltip_text("Copy VPN IP to clipboard")
        self._copy_btn.connect("clicked", self._on_copy_ip)
        btn_box.pack_end(self._copy_btn, False, False, 0)

        self.pack_start(btn_box, False, False, 0)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self.pack_start(sep, False, False, 0)

        # -- Scrollable peer list --
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(120)

        self._peer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._peer_box.set_margin_start(8)
        self._peer_box.set_margin_end(8)
        self._peer_box.set_margin_top(4)
        scroll.add(self._peer_box)
        self.pack_start(scroll, True, True, 0)

        # -- Fallback notice (shown when Tailscale absent) --
        self._fallback_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._fallback_box.set_margin_start(8)
        self._fallback_box.set_margin_end(8)
        self._fallback_box.set_margin_top(8)
        self._fallback_box.set_no_show_all(True)
        self._fallback_label = Gtk.Label(label="")
        self._fallback_label.set_xalign(0)
        self._fallback_label.set_line_wrap(True)
        self._fallback_label.set_opacity(0.6)
        self._fallback_box.pack_start(self._fallback_label, False, False, 0)
        self.pack_start(self._fallback_box, False, False, 0)

        self.show_all()

    # -- Refresh logic -------------------------------------------

    def _refresh(self):
        """Fetch status in a thread, update UI on the main loop."""
        def _worker():
            if self._mgr.is_tailscale_available():
                status = self._mgr.get_status()
                GLib.idle_add(lambda: self._apply_status(status) or GLib.SOURCE_REMOVE)
            elif self._wg.is_available():
                wg = self._wg.get_status()
                GLib.idle_add(lambda: self._apply_wg_status(wg) or GLib.SOURCE_REMOVE)
            else:
                GLib.idle_add(lambda: self._apply_not_installed() or GLib.SOURCE_REMOVE)

        threading.Thread(target=_worker, daemon=True).start()

        # Schedule next refresh
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
        self._timer_id = GLib.timeout_add_seconds(
            self._REFRESH_INTERVAL, self._on_timer_tick,
        )

    def _on_timer_tick(self):
        self._refresh()
        return GLib.SOURCE_REMOVE

    def _apply_status(self, status: TailscaleStatus):
        """Update the panel from a TailscaleStatus snapshot."""
        self._current_status = status
        if status.connected:
            self._dot.set_markup('<span foreground="#4caf50">\u25cf</span>')
            self._host_label.set_text(status.hostname or "Connected")
            self._ip_label.set_text(status.ip)
            self._toggle_btn.set_label("Disconnect")
        else:
            self._dot.set_markup('<span foreground="#f44336">\u25cf</span>')
            self._host_label.set_text("Disconnected")
            self._ip_label.set_text("")
            self._toggle_btn.set_label("Connect")

        self._fallback_box.hide()
        self._rebuild_peers(status.peers)

    def _apply_wg_status(self, wg: dict):
        """Update the panel from WireGuard fallback data."""
        self._current_status = None
        ifaces = wg.get("interfaces", [])
        if ifaces:
            first = ifaces[0]
            self._dot.set_markup('<span foreground="#2196f3">\u25cf</span>')
            self._host_label.set_text(first.get("name", "WireGuard"))
            self._ip_label.set_text(first.get("public_key", "")[:12] + "...")
        else:
            self._dot.set_markup('<span foreground="#ff9800">\u25cf</span>')
            self._host_label.set_text("WireGuard (inactive)")
            self._ip_label.set_text("")
        self._toggle_btn.set_label("--")

        # Show raw WireGuard info in fallback area
        parts = []
        for iface in ifaces:
            parts.append(f"{iface['name']}: {len(iface['peers'])} peer(s)")
        self._fallback_label.set_text(
            "\n".join(parts) if parts else "No WireGuard interfaces found"
        )
        self._fallback_box.show_all()

        # Clear peer list (WG peers are not enumerated as Tailscale peers)
        for child in self._peer_box.get_children():
            self._peer_box.remove(child)
        lbl = Gtk.Label(label="  Use 'wg show' for full details")
        lbl.set_xalign(0)
        lbl.set_opacity(0.4)
        self._peer_box.pack_start(lbl, False, False, 0)
        self._peer_box.show_all()

    def _apply_not_installed(self):
        """Show the not-installed fallback state."""
        self._current_status = None
        self._dot.set_markup('<span foreground="#888888">\u25cf</span>')
        self._host_label.set_text("VPN")
        self._ip_label.set_text("")
        self._toggle_btn.set_label("Connect")
        self._toggle_btn.set_sensitive(False)
        self._fallback_label.set_text(
            "Tailscale and WireGuard are not installed.\n"
            "Install Tailscale for VPN integration."
        )
        self._fallback_box.show_all()

        for child in self._peer_box.get_children():
            self._peer_box.remove(child)
        self._peer_box.show_all()

    def _rebuild_peers(self, peers: List[TailscalePeer]):
        """Clear and repopulate the peer list."""
        for child in self._peer_box.get_children():
            self._peer_box.remove(child)

        if not peers:
            lbl = Gtk.Label(label="  No peers")
            lbl.set_xalign(0)
            lbl.set_opacity(0.4)
            self._peer_box.pack_start(lbl, False, False, 0)
        else:
            for peer in peers:
                self._peer_box.pack_start(
                    self._make_peer_row(peer), False, False, 0,
                )

        self._peer_box.show_all()

    def _make_peer_row(self, peer: TailscalePeer) -> Gtk.Box:
        """Build a single peer row with ping button."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        # Online dot
        color = "#4caf50" if peer.online else "#888888"
        dot = Gtk.Label()
        dot.set_markup(f'<span foreground="{color}">\u25cf</span>')
        row.pack_start(dot, False, False, 0)

        # Hostname + IP + OS
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        name_lbl = Gtk.Label(label=peer.hostname or peer.ip or "unknown")
        name_lbl.set_xalign(0)
        name_lbl.set_ellipsize(3)  # Pango.EllipsizeMode.END
        name_lbl.set_max_width_chars(20)
        info.pack_start(name_lbl, False, False, 0)

        detail_parts = [p for p in (peer.ip, peer.os) if p]
        detail = Gtk.Label(label=" ".join(detail_parts))
        detail.set_xalign(0)
        detail.set_opacity(0.5)
        detail.set_ellipsize(3)
        detail.set_max_width_chars(24)
        info.pack_start(detail, False, False, 0)

        row.pack_start(info, True, True, 0)

        # Ping button
        ping_btn = Gtk.Button(label="\u21bb")
        ping_btn.set_relief(Gtk.ReliefStyle.NONE)
        ping_btn.set_tooltip_text(f"Ping {peer.hostname}")
        ping_btn.connect("clicked", self._on_ping, peer.ip, ping_btn)
        row.pack_start(ping_btn, False, False, 0)

        return row

    # -- Button handlers -----------------------------------------

    def _on_toggle(self, _btn):
        """Connect or disconnect Tailscale."""
        self._toggle_btn.set_sensitive(False)

        def _worker():
            if self._current_status and self._current_status.connected:
                ok = self._mgr.disconnect()
            else:
                ok = self._mgr.connect()
            GLib.idle_add(lambda: self._post_toggle(ok) or GLib.SOURCE_REMOVE)

        threading.Thread(target=_worker, daemon=True).start()

    def _post_toggle(self, _ok):
        self._toggle_btn.set_sensitive(True)
        self._refresh()

    def _on_copy_ip(self, _btn):
        """Copy the current VPN IP to the system clipboard."""
        ip = self._current_status.ip if self._current_status else ""
        if not ip:
            return
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(ip, -1)

    def _on_ping(self, _btn, host, btn):
        """Ping a peer and display latency on the button."""
        btn.set_sensitive(False)
        btn.set_text("...")

        def _worker():
            latency = self._mgr.ping(host)
            GLib.idle_add(
                lambda: self._on_ping_result(btn, latency) or GLib.SOURCE_REMOVE
            )

        threading.Thread(target=_worker, daemon=True).start()

    def _on_ping_result(self, btn, latency):
        btn.set_sensitive(True)
        if latency is not None:
            btn.set_text(f"{latency:.0f}ms")
            btn.set_tooltip_text(f"{latency:.1f} ms")
        else:
            btn.set_text("timeout")
            btn.set_tooltip_text("Ping failed or timed out")
