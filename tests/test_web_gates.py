#!/usr/bin/env python3
"""Web shippability gates — ISS-0021 (dashboard XSS) + ISS-0018 (traversal/CORS/auth).

Fast, daemon-free: exercises web/server.py over loopback HTTP with a stub
daemon client, and asserts on web/index.html content + esc() behavior via node.
"""
import http.client
import json
import re
import subprocess
import threading
import unittest
from http.server import HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WEB_DIR = REPO / "web"
INDEX = WEB_DIR / "index.html"

import sys
sys.path.insert(0, str(REPO / "web"))
sys.path.insert(0, str(REPO / "gui"))
import server as webserver


EVIL_NAMES = [
    '<img src=x onerror="PWNED">',
    '<script>alert(1)</script>',
    '"><script>alert(2)</script>',
    "'\"><svg onload=alert(3)>",
    '&<>"\'',
]


class _StubClient:
    def __init__(self):
        self.calls = []

    def send(self, command, args=None):
        self.calls.append((command, args or {}))
        return {"ok": True, "echo": command}

    def send_events(self, callback=None):
        import threading as _t
        return _t.Event()


def _start_server(stub):
    webserver.LmuxAPIHandler.client = stub
    httpd = HTTPServer(("127.0.0.1", 0), webserver.LmuxAPIHandler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, port


def _get(port, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers=headers or {})
    resp = conn.getresponse()
    body = resp.read()
    hdrs = dict(resp.getheaders())
    conn.close()
    return resp.status, hdrs, body


def _post(port, path, payload, headers=None):
    body = json.dumps(payload).encode()
    hdrs = {"Content-Type": "application/json",
            "Content-Length": str(len(body))}
    hdrs.update(headers or {})
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", path, body=body, headers=hdrs)
    resp = conn.getresponse()
    data = resp.read()
    h = dict(resp.getheaders())
    conn.close()
    return resp.status, h, data


def _extract_esc_source(html):
    m = re.search(r"function esc\(.*?\{.*?\n\}", html, re.S)
    return m.group(0) if m else ""


def _node_esc(payload):
    html = INDEX.read_text()
    m = re.search(r"function esc\(s\).*?return String\(s\).*?;\s*\}", html, re.S)
    if not m:
        return None
    js = m.group(0) + f"\nconsole.log(esc({json.dumps(payload)}));"
    out = subprocess.run(["node", "-e", js], capture_output=True,
                         text=True, timeout=10)
    assert out.returncode == 0, f"node failed: {out.stderr}"
    return out.stdout.rstrip("\n")


class TestDashboardXSS(unittest.TestCase):
    """ISS-0021: daemon-controlled strings must never hit innerHTML raw."""

    def test_escape_helper_exists(self):
        html = INDEX.read_text()
        self.assertIn("function esc(", html,
                      "index.html must define an esc() helper")

    def test_escape_helper_escapes_all_five_chars(self):
        for evil in EVIL_NAMES:
            got = _node_esc(evil)
            self.assertIsNotNone(got, "esc() must be extractable for node eval")
            # Escaped output must contain no raw angle brackets: without
            # a literal < or > no tag (script/img/svg) can form, and any
            # event-handler text (e.g. onerror=) is inert without markup.
            self.assertNotIn("<", got, f"raw < in {got!r}")
            self.assertNotIn(">", got, f"raw > in {got!r}")
        # exact-entity check on the canonical nasty input
        self.assertEqual(_node_esc('&<>"\''),
                         "&amp;&lt;&gt;&quot;&#39;")

    def test_no_raw_daemon_interpolation_in_renderers(self):
        html = INDEX.read_text()
        script = html.split("<script>", 1)[1]
        # Every ${...} interpolation that touches daemon-controlled fields
        # (title/name/text/message/branch/status/error/id) must go through esc(.
        daemon_pat = re.compile(
            r"\$\{[^}]*\b(ws\.title|sf\.title|w\.title|w\.git_branch|"
            r"a\.name|a\.status|n\.text|n\.message|data\.error|pane\.id)\b[^}]*\}")
        for m in daemon_pat.finditer(script):
            self.assertIn("esc(", m.group(0),
                          f"raw daemon interpolation without esc(): {m.group(0)}")

    def test_evil_names_have_no_executable_markup_after_esc(self):
        for evil in EVIL_NAMES:
            esc = _node_esc(evil)
            self.assertIsNotNone(esc)
            self.assertNotRegex(esc, r"<(script|img|svg)\b",
                                f"escaped output must not contain executable markup: {esc!r}")


class TestWebServerGates(unittest.TestCase):
    """ISS-0018: traversal containment, no ACAO*, POST token auth."""

    CANARY = REPO / "canary_traversal.css"
    CANARY_TEXT = "CANARY-TRAVERSAL-PROBE-9f8e"

    @classmethod
    def setUpClass(cls):
        cls.CANARY.write_text(cls.CANARY_TEXT)

    @classmethod
    def tearDownClass(cls):
        cls.CANARY.unlink(missing_ok=True)

    def setUp(self):
        self.stub = _StubClient()
        self.httpd, self.port = _start_server(self.stub)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_traversal_plain_dotdot_blocked(self):
        status, _, body = _get(self.port, "/../canary_traversal.css")
        self.assertEqual(status, 404)
        self.assertNotIn(self.CANARY_TEXT.encode(), body,
                         "sibling file outside web/ must not be served")

    def test_traversal_encoded_dotdot_blocked(self):
        for bad in ("/%2e%2e/canary_traversal.css",
                    "/..%2fcanary_traversal.css",
                    "/%252e%252e/canary_traversal.css",
                    "/../../etc/hostname.css"):
            status, _, body = _get(self.port, bad)
            self.assertIn(status, (400, 403, 404),
                          f"{bad} must be rejected, got {status}")
            self.assertNotIn(self.CANARY_TEXT.encode(), body)

    def test_valid_asset_still_served(self):
        status, hdrs, body = _get(self.port, "/style.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", hdrs.get("Content-Type", ""))
        self.assertTrue(len(body) > 0)

    def test_no_acao_wildcard_on_json(self):
        _, hdrs, _ = _get(self.port, "/api/ping")
        lowered = {k.lower(): v for k, v in hdrs.items()}
        self.assertNotIn("access-control-allow-origin", lowered,
                         "same-origin only: ACAO:* must be gone")

    def test_post_without_token_rejected(self):
        status, _, data = _post(self.port, "/api/command",
                                {"command": "ping", "args": {}})
        self.assertIn(status, (401, 403), f"got {status}: {data!r}")
        self.assertEqual(self.stub.calls, [],
                         "unauthenticated POST must not reach the daemon")

    def test_post_with_wrong_token_rejected(self):
        status, _, _ = _post(self.port, "/api/command",
                             {"command": "ping", "args": {}},
                             headers={"X-Lmux-Token": "wrong-token"})
        self.assertIn(status, (401, 403))

    def test_post_with_valid_token_forwarded(self):
        token = webserver.LmuxAPIHandler.web_token
        self.assertTrue(token, "server must mint a per-boot token")
        status, _, data = _post(self.port, "/api/command",
                                {"command": "ping", "args": {}},
                                headers={"X-Lmux-Token": token})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.stub.calls, [("ping", {})])
        self.assertEqual(json.loads(data).get("ok"), True)

    def test_post_token_in_body_accepted_no_query(self):
        token = webserver.LmuxAPIHandler.web_token
        status, _, _ = _post(self.port, "/api/command",
                             {"command": "ping", "args": {},
                              "token": token})
        self.assertEqual(status, 200)
        # query-string tokens must NOT authenticate (URL/token leak, N6 lesson)
        status, _, _ = _post(
            self.port, f"/api/command?token={token}",
            {"command": "ping", "args": {}})
        self.assertIn(status, (401, 403),
                      "token via query string must be rejected")

    def test_index_embeds_token_for_same_origin_js(self):
        status, _, body = _get(self.port, "/")
        self.assertEqual(status, 200)
        token = webserver.LmuxAPIHandler.web_token
        self.assertIn(token.encode(), body,
                      "dashboard must learn the token same-origin "
                      "(injected meta tag)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
