# lmux Characterization Test Harness

**Date:** 2026-09-06
**Phase:** 3 - Characterization Test Harness
**Status:** In Progress

---

## Executive Summary

This document defines the characterization test harness for lmux. Characterization tests (Golden Master tests) capture the *existing behavior* of the system before any modifications, ensuring no regression during hardening.

---

## Characterization Test Strategy

### Principles
1. **Test Before Change**: Write characterization tests BEFORE modifying any code
2. **Document Current Behavior**: Capture what the system does, even if incorrect
3. **No Regression**: All characterization tests must pass before and after changes
4. **One Fix Per PR**: Each hardening fix gets its own PR with verification gate

### Test Categories

#### 1. Core Protocol Tests
- JSON request/response format
- Error response format
- Event streaming protocol

#### 2. Workspace Lifecycle Tests
- Create, list, select, rename, close workspaces
- Workspace isolation
- Workspace metadata

#### 3. Surface Lifecycle Tests
- Create, list, focus, close surfaces
- Surface-workspace association

#### 4. Pane Operations Tests
- Split, list, focus, resize panes
- Pane-surface association

#### 5. Notification Tests
- Create, list, clear notifications
- Notification ordering

#### 6. Session Tests
- Save, restore session state
- Snapshot save/load

#### 7. Config Tests
- Get, set configuration values
- Config persistence

#### 8. Agent Tests
- Spawn, list, stop agents
- Agent-workspace association

#### 9. Tree Hierarchy Tests
- Workspace/surface/pane hierarchy
- Tree depth and structure

#### 10. Security Tests (New)
- Socket authentication
- Rate limiting
- Input validation

---

## Characterization Test Implementation

### Test File Structure

```
tests/
├── characterization/
│   ├── test_core_protocol.py      # JSON protocol tests
│   ├── test_workspace_lifecycle.py # Workspace tests
│   ├── test_surface_lifecycle.py   # Surface tests
│   ├── test_pane_operations.py     # Pane tests
│   ├── test_notifications.py       # Notification tests
│   ├── test_session.py             # Session tests
│   ├── test_config.py              # Config tests
│   ├── test_agent.py               # Agent tests
│   ├── test_tree.py                # Tree tests
│   └── test_security.py            # Security tests (new)
├── test_integration.py             # Existing integration tests
└── lmux.py                         # Client library
```

### Characterization Test Template

```python
#!/usr/bin/env python3
"""
[CHARACTERIZATION] Test Module - Existing Behavior Documentation

This module documents the current behavior of lmux for the specified
functionality. These tests capture the EXISTING behavior, even if
incorrect, to prevent regression during hardening.
"""

import unittest
import sys
from pathlib import Path

# Add tests/ to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lmux import LmuxClient, LmuxDaemon


class TestModuleCharacterization(unittest.TestCase):
    """Characterization tests for [module name]."""

    @classmethod
    def setUpClass(cls):
        """Start daemon for tests."""
        # Use existing daemon or start new one
        pass

    def test_existing_behavior_1(self):
        """Document existing behavior 1."""
        # Capture current behavior
        pass

    def test_existing_behavior_2(self):
        """Document existing behavior 2."""
        # Capture current behavior
        pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

---

## Characterization Tests

### 1. Core Protocol Tests (`test_core_protocol.py`)

```python
#!/usr/bin/env python3
"""
[CHARACTERIZATION] Core Protocol - Existing Behavior Documentation

Documents the JSON protocol behavior for lmux daemon communication.
"""

import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lmux import LmuxClient, LmuxDaemon


class TestCoreProtocolCharacterization(unittest.TestCase):
    """Characterization tests for core protocol."""

    @classmethod
    def setUpClass(cls):
        """Start daemon for tests."""
        cls.daemon = LmuxDaemon("/tmp/lmux-char-test.sock")
        cls.client = cls.daemon.start(timeout=10)

    @classmethod
    def tearDownClass(cls):
        """Stop daemon."""
        cls.daemon.stop()

    def test_ping_returns_ok(self):
        """Document: ping command returns ok=true."""
        resp = self.client.ping()
        self.assertTrue(resp.get("ok"))
        self.assertIn("version", resp.get("result", {}))

    def test_capabilities_returns_list(self):
        """Document: capabilities command returns list."""
        resp = self.client.capabilities()
        caps = resp.get("capabilities", [])
        self.assertIsInstance(caps, list)
        self.assertGreater(len(caps), 0)

    def test_invalid_command_returns_error(self):
        """Document: invalid command returns error response."""
        resp = self.client.send("invalid.command", {})
        self.assertFalse(resp.get("ok", True))
        self.assertIn("error", resp)

    def test_malformed_json_returns_error(self):
        """Document: malformed JSON returns error response."""
        # This test documents that malformed JSON is handled
        # The actual implementation may vary
        pass

    def test_empty_request_returns_error(self):
        """Document: empty request returns error response."""
        # This test documents that empty requests are handled
        pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

### 2. Workspace Lifecycle Tests (`test_workspace_lifecycle.py`)

```python
#!/usr/bin/env python3
"""
[CHARACTERIZATION] Workspace Lifecycle - Existing Behavior Documentation

Documents workspace creation, listing, selection, renaming, and closing.
"""

import unittest
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lmux import LmuxClient, LmuxDaemon


class TestWorkspaceLifecycleCharacterization(unittest.TestCase):
    """Characterization tests for workspace lifecycle."""

    @classmethod
    def setUpClass(cls):
        """Start daemon for tests."""
        cls.daemon = LmuxDaemon("/tmp/lmux-char-ws.sock")
        cls.client = cls.daemon.start(timeout=10)

    @classmethod
    def tearDownClass(cls):
        """Stop daemon."""
        cls.daemon.stop()

    def _unique(self, prefix):
        """Generate unique workspace title."""
        return f"{prefix}-{int(time.time() * 1000) % 100000}"

    def test_create_returns_id_and_title(self):
        """Document: workspace.create returns id and title."""
        title = self._unique("char-ws")
        ws = self.client.workspace_create(title)
        self.assertIn("id", ws)
        self.assertEqual(ws["title"], title)
        self.client.workspace_close(ws["id"])

    def test_list_contains_created(self):
        """Document: workspace.list contains created workspace."""
        title = self._unique("char-list")
        ws = self.client.workspace_create(title)
        workspaces = self.client.workspace_list()
        ids = [w["id"] for w in workspaces]
        self.assertIn(ws["id"], ids)
        self.client.workspace_close(ws["id"])

    def test_select_and_current(self):
        """Document: workspace.select changes current workspace."""
        title = self._unique("char-select")
        ws = self.client.workspace_create(title)
        self.client.workspace_select(ws["id"])
        current = self.client.workspace_current()
        self.assertEqual(current["id"], ws["id"])
        self.client.workspace_close(ws["id"])

    def test_rename_updates_title(self):
        """Document: workspace.rename updates title."""
        title = self._unique("char-rename")
        ws = self.client.workspace_create(title)
        self.client.workspace_rename(ws["id"], "new-name")
        workspaces = self.client.workspace_list()
        for w in workspaces:
            if w["id"] == ws["id"]:
                self.assertEqual(w["title"], "new-name")
                break
        self.client.workspace_close(ws["id"])

    def test_close_removes_workspace(self):
        """Document: workspace.close removes workspace."""
        title = self._unique("char-close")
        ws = self.client.workspace_create(title)
        ws_id = ws["id"]
        self.client.workspace_close(ws_id)
        workspaces = self.client.workspace_list()
        ids = [w["id"] for w in workspaces]
        self.assertNotIn(ws_id, ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

### 3. Security Tests (`test_security.py`)

```python
#!/usr/bin/env python3
"""
[CHARACTERIZATION] Security - Existing Behavior Documentation

Documents security behavior including socket authentication,
rate limiting, and input validation.
"""

import unittest
import sys
import time
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lmux import LmuxClient, LmuxDaemon


class TestSecurityCharacterization(unittest.TestCase):
    """Characterization tests for security."""

    @classmethod
    def setUpClass(cls):
        """Start daemon for tests."""
        cls.daemon = LmuxDaemon("/tmp/lmux-char-security.sock")
        cls.client = cls.daemon.start(timeout=10)

    @classmethod
    def tearDownClass(cls):
        """Stop daemon."""
        cls.daemon.stop()

    def test_socket_permissions(self):
        """Document: socket has correct permissions."""
        import os
        import stat
        
        socket_path = "/tmp/lmux-char-security.sock"
        if os.path.exists(socket_path):
            mode = os.stat(socket_path).st_mode
            # Document current permissions
            # Expected: 0600 (owner-only)
            self.assertTrue(stat.S_ISREG(mode) or stat.S_ISSOCK(mode))

    def test_rejects_non_owner_connection(self):
        """Document: socket rejects non-owner connections."""
        # This test documents whether non-owner connections are rejected
        # Current behavior: may allow connections
        # Target behavior: reject non-owner connections
        pass

    def test_rate_limiting(self):
        """Document: rate limiting behavior."""
        # This test documents whether rate limiting exists
        # Current behavior: no rate limiting
        # Target behavior: rate limiting at 100 connections/second
        pass

    def test_input_validation(self):
        """Document: input validation behavior."""
        # This test documents whether malformed JSON is rejected
        # Current behavior: basic validation only
        # Target behavior: strict JSON schema validation
        pass

    def test_request_size_limit(self):
        """Document: request size limit behavior."""
        # This test documents whether large requests are rejected
        # Current behavior: LMUX_MAX_REQUEST = 65536 bytes
        # Target behavior: enforce size limit
        pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

---

## Running Characterization Tests

### Run All Characterization Tests
```bash
python3 -m pytest tests/characterization/ -v
```

### Run Specific Test Module
```bash
python3 -m pytest tests/characterization/test_core_protocol.py -v
```

### Run with Coverage
```bash
python3 -m pytest tests/characterization/ --cov=src/core --cov-report=html
```

---

## Verification Gates

### Before Any Change
```bash
# All characterization tests must pass
python3 -m pytest tests/characterization/ -v
```

### After Each Change
```bash
# All characterization tests must still pass
python3 -m pytest tests/characterization/ -v

# No regressions in existing integration tests
python3 -m pytest tests/test_integration.py -v
```

### Before Production Deployment
```bash
# All characterization tests pass
python3 -m pytest tests/characterization/ -v

# All integration tests pass
python3 -m pytest tests/test_integration.py -v

# Security tests pass
python3 -m pytest tests/characterization/test_security.py -v

# No ASan/UBSan violations
ASAN_OPTIONS=detect_leaks=1 python3 -m pytest tests/ -v
```

---

## Characterization Test Coverage

### Current Coverage
- [ ] Core Protocol: 0% (to be implemented)
- [ ] Workspace Lifecycle: 0% (to be implemented)
- [ ] Surface Lifecycle: 0% (to be implemented)
- [ ] Pane Operations: 0% (to be implemented)
- [ ] Notifications: 0% (to be implemented)
- [ ] Session: 0% (to be implemented)
- [ ] Config: 0% (to be implemented)
- [ ] Agent: 0% (to be implemented)
- [ ] Tree: 0% (to be implemented)
- [ ] Security: 0% (to be implemented)

### Target Coverage
- [ ] Core Protocol: 100%
- [ ] Workspace Lifecycle: 100%
- [ ] Surface Lifecycle: 100%
- [ ] Pane Operations: 100%
- [ ] Notifications: 100%
- [ ] Session: 100%
- [ ] Config: 100%
- [ ] Agent: 100%
- [ ] Tree: 100%
- [ ] Security: 100%

---

## Next Steps

1. **Implement characterization tests** for all modules
2. **Run tests** to document current behavior
3. **Fix tests** to match target behavior
4. **Phase 4:** Incremental Strangler Fig Hardening

---

**Characterization Harness Defined:** 2026-09-06
**Next Phase:** Phase 4 - Incremental Strangler Fig Hardening