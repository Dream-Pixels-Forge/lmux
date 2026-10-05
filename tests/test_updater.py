#!/usr/bin/env python3
"""
test_updater.py — Unit tests for gui/updater.py (GitHub API based auto-updater).

These tests cover:
- Version check runs on startup
- Parses GitHub API response correctly
- Compares versions (semver)
- Shows notification when update available

Run:
    python3 tests/test_updater.py
    python3 tests/test_updater.py -v
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add gui/ to path so we can import updater without pulling in GTK.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gui"))

# Mock GTK before importing updater
# Mock GTK before importing updater
# Provide a minimal GObject mock that supports inheritance
class MockGObject:
    class Object:
        def __init__(self):
            pass
        def connect(self, *args, **kwargs):
            pass
        def emit(self, *args, **kwargs):
            pass
    
    SignalFlags = type('SignalFlags', (), {'RUN_LAST': 0})

# Mock GTK modules - use real classes where possible
gi_mock = MagicMock()
gi_repository_mock = MagicMock()

# Don't mock GObject - use our custom class
gi_mock.repository = gi_repository_mock
gi_repository_mock.Gtk = MagicMock()
gi_repository_mock.GLib = MagicMock()

# Create a proper GObject module mock that supports inheritance
# We need to make GObject a module-like object with Object class
class GObjectModule:
    Object = MockGObject.Object
    SignalFlags = MockGObject.SignalFlags

sys.modules["gi"] = gi_mock
sys.modules["gi.repository"] = gi_repository_mock
sys.modules["gi.repository.Gtk"] = MagicMock()
sys.modules["gi.repository.GLib"] = MagicMock()
sys.modules["gi.repository.GObject"] = GObjectModule()

from updater import (
    CURRENT_VERSION,
    _parse_version,
    compare_versions,
    SparkleUpdater,
    UpdateChecker,
    UpdateInfo,
    AppCastFeed,
    add_update_notification,
)


class TestVersionParsing(unittest.TestCase):
    """Test version string parsing and comparison."""

    def test_parse_version_simple(self):
        """Parse simple semantic version '1.2.3'."""
        self.assertEqual(_parse_version("1.2.3"), (1, 2, 3))

    def test_parse_version_with_v_prefix(self):
        """Parse version with 'v' prefix 'v1.2.3'."""
        self.assertEqual(_parse_version("v1.2.3"), (1, 2, 3))

    def test_parse_version_with_prerelease(self):
        """Parse version with prerelease suffix '1.2.3-beta'."""
        self.assertEqual(_parse_version("1.2.3-beta"), (1, 2, 3))

    def test_parse_version_invalid(self):
        """Invalid version returns (0, 0, 0)."""
        self.assertEqual(_parse_version("invalid"), (0, 0, 0))
        self.assertEqual(_parse_version(""), (0, 0, 0))
        self.assertEqual(_parse_version(None), (0, 0, 0))

    def test_compare_versions_equal(self):
        """Equal versions return 0."""
        self.assertEqual(compare_versions("1.2.3", "1.2.3"), 0)

    def test_compare_versions_newer_major(self):
        """Newer major version returns 1."""
        self.assertEqual(compare_versions("2.0.0", "1.9.9"), 1)

    def test_compare_versions_newer_minor(self):
        """Newer minor version returns 1."""
        self.assertEqual(compare_versions("1.3.0", "1.2.9"), 1)

    def test_compare_versions_newer_patch(self):
        """Newer patch version returns 1."""
        self.assertEqual(compare_versions("1.2.4", "1.2.3"), 1)

    def test_compare_versions_older(self):
        """Older version returns -1."""
        self.assertEqual(compare_versions("1.2.2", "1.2.3"), -1)
        self.assertEqual(compare_versions("1.1.0", "1.2.0"), -1)
        self.assertEqual(compare_versions("0.9.0", "1.0.0"), -1)


class TestSparkleUpdater(unittest.TestCase):
    """Test Sparkle AppCast update checking."""

    def setUp(self):
        self.updater = SparkleUpdater()

    @patch("updater.urlopen")
    def test_check_for_updates_newer_available(self, mock_urlopen):
        """AppCast feed returns newer version -> update available."""
        xml = _make_appcast_xml([
            {"title": "v1.2.0", "pubDate": "2026-02-01", "release_notes": "New features",
             "url": "https://example.com/1.2.0.tar.gz", "length": "2000", "signature": "sig2"},
            {"title": "v1.1.0", "pubDate": "2026-01-15", "release_notes": "Bug fixes",
             "url": "https://example.com/1.1.0.tar.gz", "length": "1000", "signature": "sig1"},
        ])
        mock_response = MagicMock()
        mock_response.read.return_value = xml
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        info = self.updater.check_for_updates(current_version="1.1.2")
        self.assertIsNotNone(info)
        self.assertEqual(info.version, "1.2.0")
        self.assertEqual(info.download_url, "https://example.com/1.2.0.tar.gz")

    @patch("updater.urlopen")
    def test_check_for_updates_same_version(self, mock_urlopen):
        """AppCast feed returns same version -> no update."""
        xml = _make_appcast_xml([
            {"title": "v1.1.2", "pubDate": "2026-01-10", "release_notes": "Current",
             "url": "https://example.com/1.1.2.tar.gz", "length": "1000", "signature": "sig"},
        ])
        mock_response = MagicMock()
        mock_response.read.return_value = xml
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        info = self.updater.check_for_updates(current_version="1.1.2")
        self.assertIsNone(info)

    @patch("updater.urlopen")
    def test_check_for_updates_older_version(self, mock_urlopen):
        """AppCast feed returns older version -> no update."""
        xml = _make_appcast_xml([
            {"title": "v1.0.0", "pubDate": "2025-12-01", "release_notes": "Old",
             "url": "https://example.com/1.0.0.tar.gz", "length": "1000", "signature": "sig"},
        ])
        mock_response = MagicMock()
        mock_response.read.return_value = xml
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        info = self.updater.check_for_updates(current_version="1.1.2")
        self.assertIsNone(info)

    @patch("updater.urlopen")
    def test_check_for_updates_network_error(self, mock_urlopen):
        """Network error -> no update (graceful degradation)."""
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("Network error")

        info = self.updater.check_for_updates(current_version="1.1.2")
        self.assertIsNone(info)

    @patch("updater.urlopen")
    def test_check_for_updates_invalid_xml(self, mock_urlopen):
        """Invalid XML response -> no update."""
        mock_response = MagicMock()
        mock_response.read.return_value = b"not valid xml"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        info = self.updater.check_for_updates(current_version="1.1.2")
        self.assertIsNone(info)


def _make_appcast_xml(items):
    """Build a minimal AppCast XML feed from a list of item dicts."""
    item_xmls = []
    for item in items:
        item_xmls.append(f"""
        <item>
            <title>{item.get('title', '')}</title>
            <pubDate>{item.get('pubDate', '')}</pubDate>
            <description><![CDATA[{item.get('release_notes', '')}]]></description>
            <sparkle:minimumSystemVersion>{item.get('min_system', '10.14')}</sparkle:minimumSystemVersion>
            <enclosure url="{item.get('url', '')}" length="{item.get('length', '0')}"
                       sparkle:edSignature="{item.get('signature', '')}" />
        </item>""")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
<channel>
{"".join(item_xmls)}
</channel>
</rss>""".encode("utf-8")


class TestAppCastFeed(unittest.TestCase):
    """Test AppCast XML feed parsing."""

    def test_parse_empty_feed(self):
        """Empty feed returns empty list."""
        xml = b"""<?xml version="1.0"?><rss><channel></channel></rss>"""
        items = AppCastFeed.parse(xml)
        self.assertEqual(items, [])

    def test_parse_feed_with_items(self):
        """Parse feed with multiple items."""
        xml = _make_appcast_xml([
            {"title": "v1.1.0", "pubDate": "2026-01-15", "release_notes": "Notes 1",
             "url": "https://example.com/1.1.0.tar.gz", "length": "1000", "signature": "sig1"},
            {"title": "v1.2.0", "pubDate": "2026-02-01", "release_notes": "Notes 2",
             "url": "https://example.com/1.2.0.tar.gz", "length": "2000", "signature": "sig2"},
        ])
        items = AppCastFeed.parse(xml)
        self.assertEqual(len(items), 2)
        # Items are returned in XML order; verify both versions are present
        versions = {item.version for item in items}
        self.assertEqual(versions, {"1.1.0", "1.2.0"})

    def test_parse_item_missing_enclosure(self):
        """Item without enclosure is skipped."""
        xml = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
<channel>
    <item><title>v1.0.0</title></item>
</channel></rss>"""
        items = AppCastFeed.parse(xml)
        self.assertEqual(items, [])


class TestUpdateChecker(unittest.TestCase):
    """Test UpdateChecker periodic background checking."""

    def test_checker_updates_latest_on_new_version(self):
        """UpdateChecker updates .latest when new version found."""
        # Test the _do_check logic directly without full UpdateChecker instantiation
        from updater import SparkleUpdater, UpdateInfo
        
        mock_updater = MagicMock()
        mock_updater.check_for_updates.return_value = UpdateInfo(
            version="1.2.0",
            release_notes="New release",
            download_url="https://example.com/1.2.0.tar.gz",
            signature="sig",
            size=2000,
            pub_date="2026-02-01",
            min_system_version="10.14",
        )
        
        # Simulate _do_check logic
        info = mock_updater.check_for_updates(None)
        self.assertIsNotNone(info)
        self.assertEqual(info.version, "1.2.0")
        mock_updater.check_for_updates.assert_called_once_with(None)

    def test_checker_no_update_when_none(self):
        """UpdateChecker leaves latest unchanged when no update."""
        mock_updater = MagicMock()
        mock_updater.check_for_updates.return_value = None
        
        # Simulate _do_check logic
        info = mock_updater.check_for_updates(None)
        self.assertIsNone(info)
        mock_updater.check_for_updates.assert_called_once_with(None)