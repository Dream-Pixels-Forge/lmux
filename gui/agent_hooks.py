"""Agent ecosystem hooks for lmux.

Provides a HookRegistry for registering and emitting events that trigger
external scripts. Events include workspace creation, closure, pane splits,
and focus changes.
"""
import os
import subprocess
import threading
import time


# Supported hook events
HOOK_EVENTS = (
    "workspace_created",
    "workspace_closed",
    "pane_split",
    "pane_focused",
    "surface_created",
    "surface_closed",
    "agent_spawned",
    "agent_stopped",
    "notification_received",
)


class HookEntry:
    """A single registered hook."""

    __slots__ = ("event", "script", "added_at")

    def __init__(self, event, script):
        self.event = event
        self.script = script
        self.added_at = time.time()

    def to_dict(self):
        return {
            "event": self.event,
            "script": self.script,
            "added_at": self.added_at,
        }


class HookRegistry:
    """Registry for agent ecosystem hooks.

    Hooks are scripts that get executed when specific events occur.
    Scripts receive event data as environment variables.

    Usage::

        registry = HookRegistry()
        registry.register("workspace_created", "/path/to/script.sh")
        registry.emit("workspace_created", workspace_id="abc", title="my-ws")
    """

    def __init__(self):
        self._hooks = {}  # event -> [HookEntry]
        self._lock = threading.Lock()
        self._recent_events = []  # last N events for UI indicators
        self._max_recent = 20

    def register(self, event, script):
        """Register a hook script for an event.

        Args:
            event: Event name (must be in HOOK_EVENTS).
            script: Path to the script to execute.

        Returns:
            True if registered, False if event unknown.
        """
        if event not in HOOK_EVENTS:
            return False

        entry = HookEntry(event, script)
        with self._lock:
            if event not in self._hooks:
                self._hooks[event] = []
            # Avoid duplicate registrations
            for existing in self._hooks[event]:
                if existing.script == script:
                    return True
            self._hooks[event].append(entry)
        return True

    def unregister(self, event, script):
        """Unregister a hook script.

        Returns:
            True if removed, False if not found.
        """
        with self._lock:
            hooks = self._hooks.get(event, [])
            for i, entry in enumerate(hooks):
                if entry.script == script:
                    hooks.pop(i)
                    return True
        return False

    def emit(self, event, **kwargs):
        """Emit an event, running all registered scripts.

        Scripts receive event data as environment variables with LMUX_HOOK_ prefix.
        Scripts are run in a background thread to avoid blocking.

        Args:
            event: Event name.
            **kwargs: Key-value data passed to scripts as env vars.
        """
        # Record recent event for UI indicators
        with self._lock:
            self._recent_events.append({
                "event": event,
                "time": time.time(),
                "data": kwargs,
            })
            if len(self._recent_events) > self._max_recent:
                self._recent_events = self._recent_events[-self._max_recent:]

        with self._lock:
            hooks = list(self._hooks.get(event, []))

        if not hooks:
            return

        def _run_hooks():
            env = os.environ.copy()
            env["LMUX_HOOK_EVENT"] = event
            for key, value in kwargs.items():
                env[f"LMUX_HOOK_{key.upper()}"] = str(value)

            for entry in hooks:
                if os.path.isfile(entry.script) and os.access(entry.script, os.X_OK):
                    try:
                        subprocess.Popen(
                            [entry.script],
                            env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    except Exception:
                        pass  # hooks should not crash the app

        threading.Thread(target=_run_hooks, daemon=True).start()

    def list_hooks(self):
        """Return all registered hooks as a dict of event -> [script paths].

        Returns:
            Dict mapping event names to lists of script paths.
        """
        with self._lock:
            return {
                event: [e.script for e in entries]
                for event, entries in self._hooks.items()
                if entries
            }

    def recent_events(self, n=5):
        """Return the last N hook events for UI display.

        Returns:
            List of event dicts with event name and time.
        """
        with self._lock:
            return list(self._recent_events[-n:])
