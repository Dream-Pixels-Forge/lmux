# lmux vs cmux — Feature Comparison & Gap Analysis

**Date:** 2026-09-07
**Purpose:** Make lmux an advanced version of cmux

---

## Executive Summary

| Aspect | cmux | lmux | Gap |
|--------|------|------|-----|
| **Platform** | macOS native (Swift/SwiftUI) | Linux (C/GTK3/VTE) | Different targets — lmux = Linux cmux |
| **Architecture** | 72 Swift packages, modular | C core + Python GUI | lmux needs modularity |
| **Terminal Engine** | Ghostty (GPU-accelerated) | VTE (GTK3) | VTE is solid but less performant |
| **GUI** | Native AppKit/SwiftUI | GTK3/VTE | GTK3 is cross-platform but less polished |
| **In-App Browser** | WKWebView with scriptable API | Stub (19 functions, 19 tests) | **PARTIAL** — needs WebKit2GTK integration |
| **AI Agent Integration** | Deep (14+ agents, hibernation, teams) | Good (spawn/list/stop/hibernate/feed) | **MOSTLY PARITY** — missing Teams modes |
| **Notification System** | Blue rings, sidebar, phone forwarding | OSC parsing, rings, hooks | **MOSTLY PARITY** — missing sidebar badges |
| **Remote SSH** | Persistent PTY, browser relay, Go daemon | Good (SSH sessions, save/restore) | **MOSTLY PARITY** — missing browser relay |
| **Cloud VMs** | Built-in VM management | None | **GAP** |
| **iOS Companion** | Yes (Founder's Edition) | None | **GAP** |
| **Security** | Socket auth modes, HMAC | SO_PEERCRED, rate limiting | lmux has basic security |
| **Observability** | Basic logging | Structured logging, metrics, health | lmux is ahead here |
| **Testing** | Xcode tests | 134 integration + 37 unit + fuzz | lmux is ahead here |

---

## Feature-by-Feature Comparison

### 1. Core Terminal Features

| Feature | cmux | lmux | Status |
|---------|------|------|--------|
| Workspace management | ✅ | ✅ | Parity |
| Split panes (H/V) | ✅ | ✅ | Parity |
| Multi-window | ✅ | ✅ | Parity |
| Session restore | ✅ | ✅ | Parity |
| Find in terminal | ✅ | ❌ | **GAP** |
| Copy mode | ✅ | ✅ | Parity |
| Tmux compatibility | ✅ | ✅ | Parity |
| Canvas layout | ✅ | ✅ | Parity |
| Right sidebar | ✅ | ❌ | **GAP** |
| File explorer | ✅ | ✅ | Parity |
| Git integration | ✅ | Basic (branch display) | Partial |
| Workspace groups | ✅ | ✅ | Parity |
| Workspace auto-naming | ✅ (AI-summarized) | ✅ (directory-based) | Partial |

### 2. In-App Browser

| Feature | cmux | lmux | Status |
|---------|------|------|--------|
| WKWebView/WebKit browser | ✅ | ✅ (stub) | Partial |
| Scriptable API (snapshot, click, fill) | ✅ | ✅ (stub) | Partial |
| Browser split alongside terminal | ✅ | ✅ (stub) | Partial |
| Browser import (Chrome, Firefox, etc.) | ✅ | ❌ | **GAP** |
| Developer tools | ✅ | ❌ | **GAP** |
| Browser focus mode | ✅ | ❌ | **GAP** |

### 3. AI Agent Integration

| Feature | cmux | lmux | Status |
|---------|------|------|--------|
| Agent hook system | ✅ (14+ agents) | ✅ (basic hooks) | Partial |
| Agent spawn/list/stop | ✅ | ✅ | Parity |
| Agent hibernation (kill/resume idle) | ✅ | ✅ | Parity |
| Claude Code Teams mode | ✅ | ❌ | **GAP** |
| Codex Teams mode | ✅ | ❌ | **GAP** |
| Feed panel (inline approvals) | ✅ | ✅ | Parity |
| Agent-specific resume commands | ✅ | ✅ | Parity |
| Workspace naming from conversation | ✅ | ❌ | **GAP** |

### 4. Notification System

| Feature | cmux | lmux | Status |
|---------|------|------|--------|
| OSC notification parsing | ✅ | ✅ | Parity |
| Blue ring highlights | ✅ | ✅ | Parity |
| Sidebar notification badges | ✅ | ❌ | **GAP** |
| Unified notification panel | ✅ | ✅ (basic list) | Partial |
| macOS system notifications | ✅ | N/A (Linux) | Platform diff |
| Phone forwarding | ✅ | ❌ | **GAP** |
| Notification sounds | ✅ | ❌ | **GAP** |
| Composable notification hooks | ✅ | ✅ | Parity |
| Notification filtering/transformation | ✅ | ❌ | **GAP** |

### 5. Remote SSH

| Feature | cmux | lmux | Status |
|---------|------|------|--------|
| SSH workspace creation | ✅ | ✅ | Parity |
| Persistent PTY sessions | ✅ | ✅ | Parity |
| Remote daemon (Go) | ✅ | ❌ | **GAP** |
| Browser relay through remote | ✅ | ❌ | **GAP** |
| CLI relay (reverse SSH) | ✅ | ❌ | **GAP** |
| Drag-and-drop upload | ✅ | ❌ | **GAP** |

### 6. Cloud VMs

| Feature | cmux | lmux | Status |
|---------|------|------|--------|
| Cloud VM management | ✅ | ❌ | **GAP** |
| WebSocket PTY transport | ✅ | ❌ | **GAP** |
| Lease-based auth | ✅ | ❌ | **GAP** |

### 7. Security (lmux ahead)

| Feature | cmux | lmux | Status |
|---------|------|------|--------|
| Socket auth modes | ✅ (5 modes) | ✅ (SO_PEERCRED) | Parity |
| Rate limiting | ❌ | ✅ (token bucket) | **lmux ahead** |
| Input validation | Basic | ✅ (size/cmd/depth) | **lmux ahead** |
| RFC 7807 errors | ❌ | ✅ | **lmux ahead** |
| Structured logging | Basic | ✅ (JSON mode) | **lmux ahead** |
| Metrics collection | ❌ | ✅ | **lmux ahead** |
| Health checks | ❌ | ✅ | **lmux ahead** |

### 8. Observability (lmux ahead)

| Feature | cmux | lmux | Status |
|---------|------|------|--------|
| Structured logging | Basic | ✅ (JSON mode) | **lmux ahead** |
| Request metrics | ❌ | ✅ | **lmux ahead** |
| Health endpoints | ❌ | ✅ | **lmux ahead** |
| Uptime tracking | ❌ | ✅ | **lmux ahead** |

### 9. Testing (lmux ahead)

| Feature | cmux | lmux | Status |
|---------|------|------|--------|
| Integration tests | Xcode tests | ✅ (134 tests) | **lmux ahead** |
| Unit tests | Xcode tests | ✅ (37 C tests) | **lmux ahead** |
| Fuzz testing | ❌ | ✅ (20K+ iterations) | **lmux ahead** |
| Performance benchmarks | ❌ | ✅ | **lmux ahead** |

---

## Priority Gaps to Close

### Phase 1: Critical (COMPLETED)
1. ✅ **In-App Browser** — Stub + dispatch + CLI (19 functions, 19 tests)
2. ✅ **Agent Hibernation** — Auto-hibernate after 300s idle, resume on demand
3. ✅ **Notification System** — Visual rings, composable hooks, CLI commands

### Phase 2: Major (COMPLETED)
4. ✅ **Multi-Window** — Window create/list/close/focus/move-workspace
5. ✅ **Persistent SSH PTY** — SSH session create/list/kill/save/restore
6. **Remote Daemon** — Go-based daemon for SSH workspaces (FUTURE)
7. ✅ **Feed Panel** — Feed panel create/list/close, entry add/list

### Phase 3: Important (COMPLETED)
8. ✅ **Tmux Compatibility** — 10 tmux commands mapped to lmux
9. ✅ **Copy Mode** — Vi-style text selection with clipboard
10. ✅ **File Explorer** — Integrated file browsing with sort/filter/search
11. ✅ **Canvas Layout** — Freeform pane positioning
12. **Right Sidebar** — Tool panels (GUI feature, not daemon)

### Phase 4: Remaining Gaps (Nice to Have)
- Find in terminal (GUI feature)
- Cloud VM management
- iOS Companion
- Browser import from Chrome/Firefox
- Developer tools integration
- Phone forwarding
- Notification sounds
- Notification filtering/transformation

### Phase 4: Advanced (Future)
13. **Cloud VM Management** — Built-in VM provisioning
14. **iOS Companion** — Mobile terminal sync
15. **Agent Teams Modes** — Claude Code Teams, Codex Teams

---

## lmux Advantages Over cmux

lmux already excels in areas where cmux is weak:

1. **Security Hardening** — Rate limiting, input validation, RFC 7807 errors
2. **Observability** — Structured logging, metrics, health checks
3. **Testing** — 77 integration + 22 unit + fuzz + benchmarks
4. **Cross-Platform Potential** — C core can target Linux (cmux is macOS-only)
5. **Lightweight** — C daemon vs 72 Swift packages
6. **Embeddable** — `liblmux_core.a` can be embedded in other tools

---

## Recommendation

**lmux should position itself as the "Linux cmux" with enterprise-grade reliability.**

Focus strategy:
1. Close the critical gaps (browser, agent hibernation, notifications)
2. Leverage lmux advantages (security, observability, testing)
3. Target Linux developers who want cmux-like features
4. Consider Wayland support (cmux is macOS-only)
