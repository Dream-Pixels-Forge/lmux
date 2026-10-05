#!/usr/bin/env python3
"""Tests for lmux web dashboard (Phase 5).

TDD: These tests are written FIRST and should FAIL (RED) before implementation.
"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path


# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestWebDashboard:
    """Test suite for the web dashboard."""

    daemon = None
    client = None
    web_server_process = None
    web_port = 18080  # Use different port to avoid conflicts

    @classmethod
    def setup_class(cls):
        """Start daemon and web server before tests."""
        from tests.lmux import LmuxDaemon

        # Start daemon
        cls.daemon = LmuxDaemon(socket_path=f"/tmp/lmux-web-test-{os.getpid()}.sock")
        cls.client = cls.daemon.start(timeout=10)

        # Start web server in background
        repo_root = Path(__file__).parent.parent
        web_script = repo_root / "web" / "server.py"
        cls.web_server_process = subprocess.Popen(
            [sys.executable, str(web_script), "--port", str(cls.web_port), "--host", "127.0.0.1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "LMUX_SOCKET_PATH": cls.daemon.socket_path},
        )

        # Wait for web server to be ready
        start = time.time()
        while time.time() - start < 10:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{cls.web_port}/api/ping", timeout=1) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode())
                        if data.get("ok"):
                            break
            except Exception:
                pass
            time.sleep(0.2)
        else:
            # Print server output for debugging
            stdout, stderr = cls.web_server_process.communicate(timeout=1)
            print(f"STDOUT: {stdout.decode()}")
            print(f"STDERR: {stderr.decode()}")
            raise TimeoutError("Web server did not start within 10s")

    @classmethod
    def teardown_class(cls):
        """Stop web server and daemon after tests."""
        if cls.web_server_process:
            cls.web_server_process.terminate()
            try:
                cls.web_server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.web_server_process.kill()
                cls.web_server_process.wait()
        if cls.daemon:
            cls.daemon.stop()

    def _api_get(self, path: str) -> dict:
        """Make GET request to web API."""
        url = f"http://127.0.0.1:{self.web_port}{path}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode())

    def _api_post(self, path: str, data: dict) -> dict:
        """Make POST request to web API."""
        url = f"http://127.0.0.1:{self.web_port}{path}"
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())

    def test_server_starts_on_port(self):
        """Test that web server starts and responds on configured port."""
        resp = self._api_get("/api/ping")
        assert resp.get("ok") is True
        assert "version" in resp

    def test_api_tree_returns_workspace_tree(self):
        """Test GET /api/tree returns workspace/surface/pane tree."""
        # Create a workspace first
        ws = self.client.workspace_create("test-workspace")
        self.client.surface_create(ws["id"], title="test-surface")

        # Give time for events to propagate
        time.sleep(0.2)

        resp = self._api_get("/api/tree")
        assert resp.get("ok") is True or "workspaces" in resp
        tree = resp.get("result", resp)
        assert "workspaces" in tree
        assert len(tree["workspaces"]) >= 1
        ws_found = next((w for w in tree["workspaces"] if w["id"] == ws["id"]), None)
        assert ws_found is not None
        assert ws_found["title"] == "test-workspace"

    def test_api_workspaces_returns_list(self):
        """Test GET /api/workspaces returns workspace list."""
        resp = self._api_get("/api/workspaces")
        assert resp.get("ok") is True or "workspaces" in resp
        workspaces = resp.get("result", resp).get("workspaces", [])
        assert isinstance(workspaces, list)

    def test_api_agents_returns_list(self):
        """Test GET /api/agents returns agent list."""
        resp = self._api_get("/api/agents")
        assert resp.get("ok") is True or "agents" in resp
        agents = resp.get("result", resp).get("agents", [])
        assert isinstance(agents, list)

    def test_api_command_executes_commands(self):
        """Test POST /api/command executes daemon commands."""
        resp = self._api_post("/api/command", {"command": "workspace.list", "args": {}})
        assert resp.get("ok") is True
        result = resp.get("result", {})
        assert "workspaces" in result

    def test_api_command_with_args(self):
        """Test POST /api/command with arguments."""
        resp = self._api_post("/api/command", {"command": "workspace.create", "args": {"title": "api-test-ws"}})
        assert resp.get("ok") is True
        result = resp.get("result", {})
        assert "id" in result
        assert result.get("title") == "api-test-ws"

    def test_api_events_sse_stream(self):
        """Test GET /api/events returns SSE stream."""
        # Make a request and check it's an SSE stream
        url = f"http://127.0.0.1:{self.web_port}/api/events"
        req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
        resp = urllib.request.urlopen(req, timeout=2)
        # Check headers
        assert resp.headers.get("Content-Type") == "text/event-stream"
        # Read first few bytes to verify SSE format
        data = resp.read(100).decode()
        assert "data:" in data or ": keepalive" in data
        resp.close()

    def test_ui_loads_at_root(self):
        """Test that UI loads at http://localhost:8080/"""
        url = f"http://127.0.0.1:{self.web_port}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Type", "").startswith("text/html")
            html = resp.read().decode()
            assert "lmux" in html.lower()
            assert "dashboard" in html.lower() or "workspace" in html.lower()

    def test_ui_loads_static_assets(self):
        """Test that CSS and JS assets load correctly."""
        # Test CSS
        url = f"http://127.0.0.1:{self.web_port}/style.css"
        with urllib.request.urlopen(url, timeout=5) as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Type", "").startswith("text/css")
            css = resp.read().decode()
            assert "--bg" in css  # CSS variables defined

    def test_real_time_updates_via_sse(self):
        """Test that SSE events trigger UI updates (integration test)."""
        events_received = []

        def collect_events():
            url = f"http://127.0.0.1:{self.web_port}/api/events"
            req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
            resp = urllib.request.urlopen(req, timeout=5)
            start = time.time()
            while time.time() - start < 3:
                line = resp.readline().decode()
                if line.startswith("data:"):
                    try:
                        evt = json.loads(line[5:].strip())
                        events_received.append(evt)
                    except json.JSONDecodeError:
                        pass
            resp.close()

        # Start SSE listener in background
        thread = threading.Thread(target=collect_events, daemon=True)
        thread.start()

        # Give time to connect
        time.sleep(0.5)

        # Create a workspace - should generate events
        ws = self.client.workspace_create("sse-test-ws")

        # Wait for event
        thread.join(timeout=3)

        # Check we received workspace-related events
        ws_events = [e for e in events_received if e.get("name", "").startswith("workspace")]
        assert len(ws_events) >= 1, f"Expected workspace events, got: {events_received}"

    def test_cors_headers_present(self):
        """Test that CORS headers are present for browser access."""
        url = f"http://127.0.0.1:{self.web_port}/api/tree"
        req = urllib.request.Request(url, headers={"Origin": "http://localhost:3000"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            # Should have CORS headers
            cors = resp.headers.get("Access-Control-Allow-Origin")
            assert cors == "*" or cors == "http://localhost:3000"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
