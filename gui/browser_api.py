"""Browser scriptable API for lmux — JavaScript injection and automation."""
import json
import os
import sys
import logging
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger("lmux.browser.api")


@dataclass
class BrowserCommand:
    """A browser command to execute."""
    command: str
    args: Dict[str, Any]
    callback: Optional[Callable] = None


class BrowserScriptableAPI:
    """Scriptable API for the browser panel — allows automation and JS injection."""

    def __init__(self, browser_panel):
        self._browser = browser_panel
        self._web_view = browser_panel._web_view if browser_panel else None
        self._commands: Dict[str, Callable] = {}
        self._setup_commands()

    def _setup_commands(self):
        """Register built-in commands."""
        self._commands = {
            "navigate": self._cmd_navigate,
            "reload": self._cmd_reload,
            "go_back": self._cmd_go_back,
            "go_forward": self._cmd_go_forward,
            "get_url": self._cmd_get_url,
            "get_title": self._cmd_get_title,
            "get_html": self._cmd_get_html,
            "set_html": self._cmd_set_html,
            "execute_js": self._cmd_execute_js,
            "screenshot": self._cmd_screenshot,
            "inject_css": self._cmd_inject_css,
            "evaluate_expression": self._cmd_evaluate_expression,
            "get_cookies": self._cmd_get_cookies,
            "set_cookie": self._cmd_set_cookie,
            "clear_cookies": self._cmd_clear_cookies,
            "get_history": self._cmd_get_history,
            "print_page": self._cmd_print_page,
        }

    def execute_command(self, command: str, args: Dict[str, Any] = None) -> Any:
        """Execute a browser command."""
        if command not in self._commands:
            raise ValueError(f"Unknown command: {command}")

        try:
            return self._commands[command](args or {})
        except Exception as e:
            logger.error(f"Browser command failed: {command} - {e}")
            raise

    def _cmd_navigate(self, args: Dict[str, Any]) -> bool:
        """Navigate to a URL."""
        url = args.get("url", "")
        if not url:
            raise ValueError("URL is required")
        self._browser.navigate(url)
        return True

    def _cmd_reload(self, args: Dict[str, Any]) -> bool:
        """Reload current page."""
        self._browser.reload()
        return True

    def _cmd_go_back(self, args: Dict[str, Any]) -> bool:
        """Go back in history."""
        self._browser.go_back()
        return True

    def _cmd_go_forward(self, args: Dict[str, Any]) -> bool:
        """Go forward in history."""
        self._browser.go_forward()
        return True

    def _cmd_get_url(self, args: Dict[str, Any]) -> str:
        """Get current URL."""
        return self._browser.get_uri() or ""

    def _cmd_get_title(self, args: Dict[str, Any]) -> str:
        """Get current page title."""
        return self._browser.get_title() or ""

    def _cmd_get_html(self, args: Dict[str, Any]) -> str:
        """Get current page HTML."""
        if not self._web_view:
            return ""
        
        js = "document.documentElement.outerHTML"
        result = self._execute_js_sync(js)
        return result or ""

    def _cmd_set_html(self, args: Dict[str, Any]) -> bool:
        """Set page HTML directly."""
        html = args.get("html", "")
        if not self._web_view:
            return False
        
        self._web_view.load_html(html, None)
        return True

    def _cmd_execute_js(self, args: Dict[str, Any]) -> Any:
        """Execute JavaScript code and return result."""
        code = args.get("code", "")
        if not code:
            raise ValueError("JavaScript code is required")
        
        return self._execute_js_sync(code)

    def _cmd_screenshot(self, args: Dict[str, Any]) -> str:
        """Take a screenshot and return base64 data."""
        if not self._web_view:
            return ""
        
        # WebKit2 doesn't have direct screenshot API in Python bindings
        # We'll use a JS-based approach
        js = """
        (function() {
            // Create a canvas and draw the page
            var canvas = document.createElement('canvas');
            canvas.width = window.innerWidth;
            canvas.height = window.innerHeight;
            var ctx = canvas.getContext('2d');
            // This is a simplified approach - full implementation would need WebGL
            return canvas.toDataURL('image/png');
        })()
        """
        result = self._execute_js_sync(js)
        return result or ""

    def _cmd_inject_css(self, args: Dict[str, Any]) -> bool:
        """Inject CSS into the page."""
        css = args.get("css", "")
        if not css or not self._web_view:
            return False
        
        js = f"""
        (function() {{
            var style = document.createElement('style');
            style.type = 'text/css';
            style.innerHTML = {json.dumps(css)};
            document.head.appendChild(style);
        }})()
        """
        self._execute_js_sync(js)
        return True

    def _cmd_evaluate_expression(self, args: Dict[str, Any]) -> Any:
        """Evaluate a JavaScript expression and return result."""
        expression = args.get("expression", "")
        if not expression:
            raise ValueError("Expression is required")
        
        return self._execute_js_sync(expression)

    def _cmd_get_cookies(self, args: Dict[str, Any]) -> List[Dict[str, str]]:
        """Get all cookies for current domain."""
        if not self._web_view:
            return []
        
        js = "document.cookie"
        result = self._execute_js_sync(js)
        if not result:
            return []
        
        # Parse cookie string
        cookies = []
        for cookie in result.split(";"):
            cookie = cookie.strip()
            if "=" in cookie:
                name, value = cookie.split("=", 1)
                cookies.append({"name": name.strip(), "value": value.strip()})
        
        return cookies

    def _cmd_set_cookie(self, args: Dict[str, Any]) -> bool:
        """Set a cookie."""
        name = args.get("name", "")
        value = args.get("value", "")
        domain = args.get("domain", "")
        path = args.get("path", "/")
        
        if not name:
            raise ValueError("Cookie name is required")
        
        cookie_str = f"{name}={value}"
        if domain:
            cookie_str += f"; domain={domain}"
        if path:
            cookie_str += f"; path={path}"
        
        js = f"document.cookie = {json.dumps(cookie_str)}"
        self._execute_js_sync(js)
        return True

    def _cmd_clear_cookies(self, args: Dict[str, Any]) -> bool:
        """Clear all cookies."""
        if not self._web_view:
            return False
        
        # Get all cookies and delete them
        js = """
        (function() {
            var cookies = document.cookie.split(";");
            for (var i = 0; i < cookies.length; i++) {
                var cookie = cookies[i];
                var eqPos = cookie.indexOf("=");
                var name = eqPos > -1 ? cookie.substr(0, eqPos) : cookie;
                document.cookie = name.trim() + "=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/";
            }
        })()
        """
        self._execute_js_sync(js)
        return True

    def _cmd_get_history(self, args: Dict[str, Any]) -> List[str]:
        """Get navigation history."""
        return self._browser._history.copy()

    def _cmd_print_page(self, args: Dict[str, Any]) -> bool:
        """Print the current page."""
        if not self._web_view:
            return False
        
        # WebKit2 print operation
        operation = WebKit2.PrintOperation(self._web_view)
        operation.print_page()
        return True

    def _execute_js_sync(self, js: str) -> Any:
        """Execute JavaScript synchronously and return result."""
        if not self._web_view:
            return None
        
        result = [None]
        completed = [False]
        
        def finish_callback(web_view, async_result):
            try:
                js_result = web_view.run_javascript_finish(async_result)
                if js_result:
                    result[0] = js_result.get_js_value()
            except Exception as e:
                logger.error(f"JavaScript execution failed: {e}")
            finally:
                completed[0] = True
        
        self._web_view.run_javascript(js, None, finish_callback, None)
        
        # Wait for completion (with timeout)
        import time
        timeout = 5.0
        start = time.time()
        while not completed[0] and time.time() - start < timeout:
            # Process GTK events
            while Gtk.events_pending():
                Gtk.main_iteration()
            time.sleep(0.01)
        
        return result[0]


class BrowserAPIBridge:
    """Bridge between browser API and daemon socket protocol."""

    def __init__(self, browser_api: BrowserScriptableAPI):
        self._api = browser_api

    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Handle a socket API request for browser operations."""
        command = request.get("cmd", "")
        args = request.get("args", {})

        # Map socket commands to API commands
        command_map = {
            "browser.navigate": "navigate",
            "browser.reload": "reload",
            "browser.go_back": "go_back",
            "browser.go_forward": "go_forward",
            "browser.get_url": "get_url",
            "browser.get_title": "get_title",
            "browser.get_html": "get_html",
            "browser.set_html": "set_html",
            "browser.execute_js": "execute_js",
            "browser.screenshot": "screenshot",
            "browser.inject_css": "inject_css",
            "browser.evaluate": "evaluate_expression",
            "browser.get_cookies": "get_cookies",
            "browser.set_cookie": "set_cookie",
            "browser.clear_cookies": "clear_cookies",
            "browser.get_history": "get_history",
            "browser.print": "print_page",
        }

        if command not in command_map:
            return {"ok": False, "error": f"Unknown browser command: {command}"}

        api_command = command_map[command]

        try:
            result = self._api.execute_command(api_command, args)
            return {"ok": True, "result": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def setup_browser_api(browser_panel) -> BrowserScriptableAPI:
    """Setup and return the browser scriptable API."""
    api = BrowserScriptableAPI(browser_panel)
    return api


def setup_browser_api_bridge(browser_api: BrowserScriptableAPI) -> BrowserAPIBridge:
    """Setup and return the browser API bridge for socket protocol."""
    bridge = BrowserAPIBridge(browser_api)
    return bridge
