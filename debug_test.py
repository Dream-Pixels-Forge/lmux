import sys
sys.path.insert(0, 'gui')
from unittest.mock import MagicMock, patch
sys.modules['gi'] = MagicMock()
sys.modules['gi.repository'] = MagicMock()
sys.modules['gi.repository.GObject'] = MagicMock()
sys.modules['gi.repository.GLib'] = MagicMock()

from updater import UpdateChecker, SparkleUpdater

# Test 1: Direct call to check_for_updates
print("Test 1: Direct call to SparkleUpdater.check_for_updates")
updater = SparkleUpdater()
mock_urlopen = MagicMock()
with patch('updater.urlopen', mock_urlopen):
    mock_response = MagicMock()
    mock_response.read.return_value = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
<channel>
<item>
<title>v1.0.0</title>
<pubDate>2025-12-01</pubDate>
<description>Old</description>
<enclosure url="https://example.com/1.0.0.tar.gz" length="1000" sparkle:edSignature="sig" />
</item>
</channel></rss>"""
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_urlopen.return_value = mock_response
    info = updater.check_for_updates(current_version="1.1.2")
    print(f"Result: {info}")

# Test 2: UpdateChecker with mock
print("\nTest 2: UpdateChecker._do_check with mock")
c = UpdateChecker()
c._updater = MagicMock()
c._updater.check_for_updates.return_value = None
print('Before _do_check')
c._do_check()
print('After _do_check')
print('Call count:', c._updater.check_for_updates.call_count)
print('Call args:', c._updater.check_for_updates.call_args)

# Test 3: Check if _do_check is actually being called
print("\nTest 3: Check _do_check method")
print(f"_do_check method: {UpdateChecker._do_check}")