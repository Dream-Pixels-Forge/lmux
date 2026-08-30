"""Integration helpers for the auto-naming engine.

These thin wrappers are meant to be called from main.py / sidebar.py
hooks (pane split, focus change) to automatically name workspaces
using the AutoNamer engine.
"""
import threading

from auto_naming import AutoNamer, get_namer


def _resolve_pid(pane):
    """Best-effort extraction of a shell PID from a VTE terminal widget."""
    try:
        # VTE widgets expose the child PID via the pty
        pid = pane.get_child_pid() if hasattr(pane, "get_child_pid") else None
        if pid and pid > 0:
            return pid
    except Exception:
        pass
    return None


def _resolve_working_dir(pane):
    """Best-effort extraction of the working directory from a pane."""
    # If the pane exposes cwd directly, use it
    cwd = getattr(pane, "cwd", None) or getattr(pane, "working_dir", None)
    if cwd:
        return cwd
    # Fallback: try /proc/<pid>/cwd
    pid = _resolve_pid(pane)
    if pid:
        try:
            import os
            return os.path.realpath(f"/proc/{pid}/cwd")
        except (FileNotFoundError, OSError):
            pass
    return None


def auto_rename_on_split(pane_id, working_dir, pane=None):
    """Called after a pane is split to suggest a name for the workspace.

    Args:
        pane_id: identifier of the newly created pane
        working_dir: working directory of the new pane
        pane: (optional) the VTE terminal widget for PID resolution
    """
    namer = get_namer()
    pid = _resolve_pid(pane) if pane else None
    name = namer.suggest_name(pane_id, working_dir, pid=pid)
    namer.rename_workspace(pane_id, name)
    return name


def auto_rename_on_focus(pane_id, old_pane_id, pane=None):
    """Called on focus change to potentially update the workspace name.

    Args:
        pane_id: identifier of the newly focused pane
        old_pane_id: identifier of the previously focused pane
        pane: (optional) the VTE terminal widget for PID resolution
    """
    namer = get_namer()
    working_dir = _resolve_working_dir(pane) if pane else None
    if working_dir is None:
        return None
    pid = _resolve_pid(pane) if pane else None
    name = namer.suggest_name(pane_id, working_dir, pid=pid)
    namer.rename_workspace(pane_id, name)
    return name


def get_naming_suggestions(working_dir, pane=None, max_results=3):
    """Return up to *max_results* candidate names for the given context.

    Tries each naming rule independently and collects unique results.
    Falls back to 'Untitled' only if no rule produces anything.

    Args:
        working_dir: absolute path of the working directory
        pane: (optional) the VTE terminal widget for PID resolution
        max_results: maximum suggestions to return (default 3)
    """
    namer = get_namer()
    pid = _resolve_pid(pane) if pane else None
    context = {
        "pane_id": None,
        "working_dir": working_dir,
        "pid": pid,
    }
    suggestions = []
    seen = set()
    for rule in namer._rules:
        try:
            name = rule.generate_name(context)
            if name:
                name = name.strip()
                if name and name not in seen:
                    seen.add(name)
                    suggestions.append(name)
                    if len(suggestions) >= max_results:
                        break
        except Exception:
            continue
    if not suggestions:
        suggestions.append("Untitled")
    return suggestions
