"""Convenience wrappers for emitting lifecycle hooks from lmux GUI code.

Usage from any GUI module::

    from agent_hooks_integration import (
        emit_workspace_created,
        emit_pane_split,
        emit_agent_spawned,
        emit_hook,
        HookEvent,
    )

    emit_workspace_created("ws-42")
    emit_pane_split(workspace_id="ws-42", pane_id="p-7")
    emit_agent_spawned(workspace_id="ws-42", metadata={"command": "bash"})
"""
from __future__ import annotations

import sys
import os
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_hooks import HookEvent, HookManager  # noqa: E402


# ── Core helper ─────────────────────────────────────────────


def emit_hook(
    event: HookEvent,
    workspace_id: Optional[str] = None,
    pane_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Emit a hook event through the singleton manager.

    This is the single entry-point used by all helpers below.
    """
    HookManager.instance().emit(
        event,
        workspace_id=workspace_id,
        pane_id=pane_id,
        metadata=metadata,
    )


# ── Typed convenience functions ─────────────────────────────


def emit_workspace_created(
    workspace_id: str, title: Optional[str] = None
) -> None:
    meta: Dict[str, Any] = {}
    if title:
        meta["title"] = title
    emit_hook(HookEvent.WORKSPACE_CREATED, workspace_id=workspace_id, metadata=meta)


def emit_workspace_closed(workspace_id: str) -> None:
    emit_hook(HookEvent.WORKSPACE_CLOSED, workspace_id=workspace_id)


def emit_workspace_renamed(
    workspace_id: str, old_name: Optional[str] = None, new_name: Optional[str] = None
) -> None:
    meta: Dict[str, Any] = {}
    if old_name:
        meta["old_name"] = old_name
    if new_name:
        meta["new_name"] = new_name
    emit_hook(HookEvent.WORKSPACE_RENAMED, workspace_id=workspace_id, metadata=meta)


def emit_pane_split(
    workspace_id: str,
    pane_id: str,
    direction: Optional[str] = None,
    command: Optional[str] = None,
) -> None:
    meta: Dict[str, Any] = {}
    if direction:
        meta["direction"] = direction
    if command:
        meta["command"] = command
    emit_hook(
        HookEvent.PANE_SPLIT,
        workspace_id=workspace_id,
        pane_id=pane_id,
        metadata=meta,
    )


def emit_pane_closed(workspace_id: str, pane_id: str) -> None:
    emit_hook(HookEvent.PANE_CLOSED, workspace_id=workspace_id, pane_id=pane_id)


def emit_pane_focus(
    workspace_id: str, pane_id: str, previous_pane_id: Optional[str] = None
) -> None:
    meta: Dict[str, Any] = {}
    if previous_pane_id:
        meta["previous_pane_id"] = previous_pane_id
    emit_hook(
        HookEvent.PANE_FOCUS,
        workspace_id=workspace_id,
        pane_id=pane_id,
        metadata=meta,
    )


def emit_session_start(session_id: Optional[str] = None) -> None:
    meta: Dict[str, Any] = {}
    if session_id:
        meta["session_id"] = session_id
    emit_hook(HookEvent.SESSION_START, metadata=meta)


def emit_session_end(session_id: Optional[str] = None) -> None:
    meta: Dict[str, Any] = {}
    if session_id:
        meta["session_id"] = session_id
    emit_hook(HookEvent.SESSION_END, metadata=meta)


def emit_agent_spawned(
    workspace_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    command: Optional[str] = None,
) -> None:
    meta: Dict[str, Any] = {}
    if agent_id:
        meta["agent_id"] = agent_id
    if command:
        meta["command"] = command
    emit_hook(
        HookEvent.AGENT_SPAWNED,
        workspace_id=workspace_id,
        metadata=meta,
    )


def emit_agent_stopped(
    workspace_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    exit_code: Optional[int] = None,
) -> None:
    meta: Dict[str, Any] = {}
    if agent_id:
        meta["agent_id"] = agent_id
    if exit_code is not None:
        meta["exit_code"] = exit_code
    emit_hook(
        HookEvent.AGENT_STOPPED,
        workspace_id=workspace_id,
        metadata=meta,
    )
