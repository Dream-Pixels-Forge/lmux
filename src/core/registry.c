/*
 * registry.c — Table-driven command registry (M1a).
 *
 * Additive Strangler-Fig slice: this table describes the commands the
 * daemon can dispatch, but it does not replace the dispatch chain in
 * model.c. `list-commands` is the first consumer; later slices will
 * migrate the `strcmp(cmd, "...")` branches onto this table.
 */
#include "lmux.h"
#include <string.h>

static const lmux_cmd_entry lmux_cmd_table[] = {
    /* Workspaces */
    { "workspace.create",         "Create a new workspace", "Usage: workspace.create <title>\nCreates a new workspace with the given title.\nArguments:\n  <title>  Workspace title (string)" },
    { "workspace.list",           "List all workspaces", "Usage: workspace.list\nLists all workspaces." },
    { "workspace.close",          "Close a workspace", "Usage: workspace.close <id>\nCloses the workspace with the given ID.\nArguments:\n  <id>  Workspace ID (integer)" },
    { "workspace.select",         "Focus a workspace", "Usage: workspace.select <id>\nFocuses the workspace with the given ID.\nArguments:\n  <id>  Workspace ID (integer)" },
    { "workspace.current",        "Show the focused workspace", "Usage: workspace.current\nShows the currently focused workspace." },
    { "workspace.rename",         "Rename a workspace", "Usage: workspace.rename <id> <title>\nRenames the workspace with the given ID.\nArguments:\n  <id>   Workspace ID (integer)\n  <title> New workspace title (string)" },
    { "workspace.reorder",        "Reorder workspaces", "Usage: workspace.reorder <from> <to>\nReorders workspaces from position <from> to position <to>.\nArguments:\n  <from> Source position (integer)\n  <to>   Target position (integer)" },
    { "workspace.refresh",        "Refresh workspace git/branch/port metadata", "Usage: workspace.refresh [ws_id]\nRefreshes git/branch/port metadata for a workspace.\nArguments:\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)" },
    { "workspace.env",            "Set workspace environment variables", "Usage: workspace.env <ws_id> <key>=<value> [key2=value2...]\nSets environment variables for a workspace.\nArguments:\n  <ws_id>  Workspace ID (integer)\n  <key>=<value>  Environment variable assignment (can specify multiple)" },
    { "workspace.group.create",   "Create a workspace group", "Usage: workspace.group.create <name>\nCreates a new workspace group with the given name.\nArguments:\n  <name>  Group name (string)" },
    { "workspace.group.list",     "List workspace groups", "Usage: workspace.group.list\nLists all workspace groups." },
    { "workspace.group.add",      "Add a workspace to a group", "Usage: workspace.group.add <name> <ws_id>\nAdds a workspace to a group.\nArguments:\n  <name>  Group name (string)\n  <ws_id> Workspace ID (integer)" },
    { "workspace.group.remove",   "Remove a workspace from a group", "Usage: workspace.group.remove <name> <ws_id>\nRemoves a workspace from a group.\nArguments:\n  <name>  Group name (string)\n  <ws_id> Workspace ID (integer)" },

    /* Surfaces (horizontal tabs) */
    { "surface.create",           "Create a surface (horizontal tab)", "Usage: surface.create [ws_id] [title]\nCreates a new surface (horizontal tab).\nArguments:\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)\n  [title]  Surface title (string, optional)" },
    { "surface.list",             "List surfaces", "Usage: surface.list [ws_id]\nLists all surfaces.\nArguments:\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)" },
    { "surface.close",            "Close a surface", "Usage: surface.close <id> [ws_id]\nCloses the surface with the given ID.\nArguments:\n  <id>     Surface ID (integer)\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)" },
    { "surface.split",            "Split a surface into panes", "Usage: surface.split <dir> [s_id] [ws]\nSplits a surface into panes.\nArguments:\n  <dir>    Direction: h (horizontal) or v (vertical)\n  [s_id]   Surface ID (integer, optional, defaults to current surface)\n  [ws]     Workspace ID (integer, optional, defaults to current workspace)" },
    { "surface.focus",            "Focus a surface", "Usage: surface.focus <id> [ws_id]\nFocuses the surface with the given ID.\nArguments:\n  <id>     Surface ID (integer)\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)" },
    { "surface.rename",           "Rename a surface", "Usage: surface.rename <id> [ws_id] <title>\nRenames the surface with the given ID.\nArguments:\n  <id>     Surface ID (integer)\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)\n  <title>  New surface title (string)" },
    { "surface.send_text",        "Send text to a surface", "Usage: surface.send_text <text> [s_id] [ws]\nSends text to a surface.\nArguments:\n  <text>   Text to send (string)\n  [s_id]   Surface ID (integer, optional, defaults to current surface)\n  [ws]     Workspace ID (integer, optional, defaults to current workspace)" },
    { "surface.send_key",         "Send a key to a surface", "Usage: surface.send_key <key> [s_id] [ws]\nSends a key to a surface.\nArguments:\n  <key>    Key to send (string, e.g., \"C-c\" for Ctrl+C)\n  [s_id]   Surface ID (integer, optional, defaults to current surface)\n  [ws]     Workspace ID (integer, optional, defaults to current workspace)" },
    { "surface.set_sync_panes",   "Toggle synchronized panes on a surface", "Usage: surface.set_sync_panes <id> [ws_id]\nToggles synchronized panes on a surface.\nArguments:\n  <id>     Surface ID (integer)\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)" },
    { "surface.set_window_size",  "Set surface window size", "Usage: surface.set_window_size <id> [ws_id] <width> <height>\nSets the window size for a surface.\nArguments:\n  <id>     Surface ID (integer)\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)\n  <width>  Width in pixels (integer)\n  <height> Height in pixels (integer)" },
    { "surface.canvas.enable",    "Enable free-form canvas layout", "Usage: surface.canvas.enable <id> [ws_id]\nEnables free-form canvas layout for a surface.\nArguments:\n  <id>     Surface ID (integer)\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)" },
    { "surface.canvas.disable",   "Disable canvas layout", "Usage: surface.canvas.disable <id> [ws_id]\nDisables canvas layout for a surface.\nArguments:\n  <id>     Surface ID (integer)\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)" },
    { "surface.canvas.move_pane", "Move a pane on the canvas", "Usage: surface.canvas.move_pane <id> [ws_id] <x> <y>\nMoves a pane on the canvas.\nArguments:\n  <id>     Surface ID (integer)\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)\n  <x>      X coordinate (integer)\n  <y>      Y coordinate (integer)" },
    { "surface.canvas.resize_pane", "Resize a pane on the canvas", "Usage: surface.canvas.resize_pane <id> [ws_id] <width> <height>\nResizes a pane on the canvas.\nArguments:\n  <id>     Surface ID (integer)\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)\n  <width>  Width in pixels (integer)\n  <height> Height in pixels (integer)" },
    { "surface.canvas.set_z",     "Set canvas pane z-order", "Usage: surface.canvas.set_z <id> [ws_id] <z>\nSets the Z-order of a pane on the canvas.\nArguments:\n  <id>     Surface ID (integer)\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)\n  <z>      Z-order (integer)" },
    { "surface.canvas.get_layout", "Get canvas layout", "Usage: surface.canvas.get_layout <id> [ws_id]\nGets the canvas layout for a surface.\nArguments:\n  <id>     Surface ID (integer)\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)" },
    { "surface.canvas.set_layout", "Set canvas layout", "Usage: surface.canvas.set_layout <id> [ws_id] <layout>\nSets the canvas layout for a surface.\nArguments:\n  <id>     Surface ID (integer)\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)\n  <layout> Layout specification (string)" },

    /* Panes */
    { "pane.list",                "List panes", "Usage: pane.list [ws_id]\nLists all panes.\nArguments:\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)" },
    { "pane.focus",               "Focus a pane", "Usage: pane.focus <id> [ws_id]\nFocuses the pane with the given ID.\nArguments:\n  <id>     Pane ID (integer)\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)" },
    { "pane.resize",              "Resize a pane", "Usage: pane.resize <dir> [cols] [rows] [s_id] [ws]\nResizes a pane.\nArguments:\n  <dir>    Direction: h (horizontal) or v (vertical)\n  [cols]   New width in columns (integer, optional)\n  [rows]   New height in rows (integer, optional)\n  [s_id]   Surface ID (integer, optional, defaults to current surface)\n  [ws]     Workspace ID (integer, optional, defaults to current workspace)" },
    { "pane.focus_dir",           "Move pane focus in a direction", "Usage: pane.focus_dir <dx> [dy] [s_id] [ws]\nMoves pane focus in a direction.\nArguments:\n  <dx>     X offset (integer, e.g., 1 for right, -1 for left)\n  [dy]     Y offset (integer, e.g., 1 for down, -1 for up, optional, defaults to 0)\n  [s_id]   Surface ID (integer, optional, defaults to current surface)\n  [ws]     Workspace ID (integer, optional, defaults to current workspace)" },
    { "pane.set_remain_on_exit",  "Set remain-on-exit for a pane", "Usage: pane.set_remain_on_exit <id> [ws_id] <value>\nSets remain-on-exit for a pane.\nArguments:\n  <id>     Pane ID (integer)\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)\n  <value>  Boolean value (0 or 1)" },
    { "pane.get_remain_on_exit",  "Get remain-on-exit for a pane", "Usage: pane.get_remain_on_exit <id> [ws_id]\nGets remain-on-exit for a pane.\nArguments:\n  <id>     Pane ID (integer)\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)" },
    { "pane.copy_mode.enter",     "Enter pane copy mode", "Usage: pane.copy_mode.enter [s_id] [ws]\nEnters pane copy mode.\nArguments:\n  [s_id]   Surface ID (integer, optional, defaults to current surface)\n  [ws]     Workspace ID (integer, optional, defaults to current workspace)" },
    { "pane.copy_mode.exit",      "Exit pane copy mode", "Usage: pane.copy_mode.exit [s_id] [ws]\nExits pane copy mode.\nArguments:\n  [s_id]   Surface ID (integer, optional, defaults to current surface)\n  [ws]     Workspace ID (integer, optional, defaults to current workspace)" },
    { "pane.copy_mode.move",      "Move cursor in copy mode", "Usage: pane.copy_mode.move <dx> <dy> [s_id] [ws]\nMoves cursor in copy mode.\nArguments:\n  <dx>     X offset (integer)\n  <dy>     Y offset (integer)\n  [s_id]   Surface ID (integer, optional, defaults to current surface)\n  [ws]     Workspace ID (integer, optional, defaults to current workspace)" },
    { "pane.copy_mode.select_start", "Begin copy-mode selection", "Usage: pane.copy_mode.select_start [s_id] [ws]\nBegins copy-mode selection.\nArguments:\n  [s_id]   Surface ID (integer, optional, defaults to current surface)\n  [ws]     Workspace ID (integer, optional, defaults to current workspace)" },
    { "pane.copy_mode.select_end",   "End copy-mode selection", "Usage: pane.copy_mode.select_end [s_id] [ws]\nEnds copy-mode selection.\nArguments:\n  [s_id]   Surface ID (integer, optional, defaults to current surface)\n  [ws]     Workspace ID (integer, optional, defaults to current workspace)" },
    { "pane.copy_mode.yank",      "Yank copy-mode selection", "Usage: pane.copy_mode.yank [s_id] [ws]\nYanks copy-mode selection.\nArguments:\n  [s_id]   Surface ID (integer, optional, defaults to current surface)\n  [ws]     Workspace ID (integer, optional, defaults to current workspace)" },
    { "pane.copy_mode.paste",     "Paste from copy mode", "Usage: pane.copy_mode.paste [s_id] [ws]\nPastes from copy mode.\nArguments:\n  [s_id]   Surface ID (integer, optional, defaults to current surface)\n  [ws]     Workspace ID (integer, optional, defaults to current workspace)" },

    /* Paste buffers */
    { "buffer.set",               "Set a paste buffer", "Usage: buffer.set [name] <data>\nSets a paste buffer.\nArguments:\n  [name]  Buffer name (string, optional, auto-generated if omitted)\n  <data>  Data to store in the buffer (string)" },
    { "buffer.get",               "Get a paste buffer", "Usage: buffer.get <name>\nGets a paste buffer.\nArguments:\n  <name>  Buffer name (string)" },
    { "buffer.list",              "List paste buffers", "Usage: buffer.list\nLists all paste buffers." },
    { "buffer.delete",            "Delete a paste buffer", "Usage: buffer.delete <name>\nDeletes a paste buffer.\nArguments:\n  <name>  Buffer name (string)" },
    { "buffer.save",              "Save a buffer to a file", "Usage: buffer.save <name> <path>\nSaves a buffer to a file.\nArguments:\n  <name>  Buffer name (string)\n  <path>  File path (string)" },
    { "buffer.load",              "Load a buffer from a file", "Usage: buffer.load <name> <path>\nLoads a buffer from a file.\nArguments:\n  <name>  Buffer name (string)\n  <path>  File path (string)" },
    { "buffer.capture_pane",      "Capture pane content into a buffer", "Usage: buffer.capture_pane [-S N] [-E N] [surface_id] [workspace_id] <buffer_name>\nCaptures pane content into a buffer.\nArguments:\n  [-S N]  Start line (integer, optional)\n  [-E N]  End line (integer, optional)\n  [surface_id]  Surface ID (integer, optional, defaults to current surface)\n  [workspace_id]  Workspace ID (integer, optional, defaults to current workspace)\n  <buffer_name>  Buffer name (string)" },
    { "buffer.limit",             "Set or get the paste buffer limit", "Usage: buffer.limit [N]\nSets or gets the paste buffer limit.\nArguments:\n  [N]  Limit (integer, 0=unlimited, optional, defaults to showing current limit)" },
    { "buffer.count",             "Count paste buffers", "Usage: buffer.count\nGets the count of paste buffers." },

    /* Notifications */
    { "notification.create",      "Create a notification", "Usage: notification.create <text>\nCreates a notification.\nArguments:\n  <text>  Notification text (string)" },
    { "notification.list",        "List notifications", "Usage: notification.list\nLists all notifications." },
    { "notification.clear",       "Clear notifications", "Usage: notification.clear\nClears all notifications." },
    { "notification.mark_read",   "Mark a notification read", "Usage: notification.mark_read <seq>\nMarks a notification as read.\nArguments:\n  <seq>   Notification sequence ID (integer)" },
    { "notification.ring.add",    "Add a notification ring to a pane", "Usage: notification.ring.add <pane_id> [reason]\nAdds a notification ring to a pane.\nArguments:\n  <pane_id> Pane ID (integer)\n  [reason]  Reason for the ring (string, optional)" },
    { "notification.ring.clear",  "Clear a notification ring", "Usage: notification.ring.clear <pane_id>\nClears a notification ring from a pane.\nArguments:\n  <pane_id> Pane ID (integer)" },
    { "notification.ring.list",   "List notification rings", "Usage: notification.ring.list\nLists all active notification rings." },
    { "notification.hooks.add",   "Add a notification hook", "Usage: notification.hooks.add <event> [filter] [redirect]\nAdds a notification hook.\nArguments:\n  <event>   Event name (string)\n  [filter]  Filter pattern (string, optional)\n  [redirect] Redirect target (string, optional)" },
    { "notification.hooks.remove","Remove a notification hook", "Usage: notification.hooks.remove <event> [filter]\nRemoves a notification hook.\nArguments:\n  <event>   Event name (string)\n  [filter]  Filter pattern (string, optional)" },
    { "notification.hooks.list",  "List notification hooks", "Usage: notification.hooks.list\nLists all notification hooks." },
    { "notification.hook.list",   "List notification hooks", "Usage: notification.hook.list\nLists all notification hooks (alias for notification.hooks.list)." },
    { "notification.hook.remove", "Remove a notification hook", "Usage: notification.hook.remove <event> [filter]\nRemoves a notification hook (alias for notification.hooks.remove).\nArguments:\n  <event>   Event name (string)\n  [filter]  Filter pattern (string, optional)" },

    /* Session / snapshot */
    { "snapshot.save",            "Save a session snapshot", "Usage: snapshot.save [path]\nSaves a session snapshot to a file.\nArguments:\n  [path]  File path (string, optional, defaults to standard snapshot location)" },
    { "snapshot.load",            "Load a session snapshot", "Usage: snapshot.load [path]\nLoads a session snapshot from a file.\nArguments:\n  [path]  File path (string, optional, defaults to standard snapshot location)" },
    { "session.save",             "Save the session to disk", "Usage: session.save\nSaves the session to disk." },
    { "session.restore",          "Restore the session from disk", "Usage: session.restore\nRestores the session from disk." },

    /* Configuration */
    { "config.get",               "Get configuration values", "Usage: config.get [key]\nGets configuration values.\nArguments:\n  [key]  Configuration key (string, optional, omits to get all values)" },
    { "config.set",               "Set a configuration value", "Usage: config.set <key> <value>\nSets a configuration value.\nArguments:\n  <key>   Configuration key (string)\n  <value> Configuration value (string)" },
    { "reload-config",            "Reload configuration", "Usage: reload-config\nReloads configuration from file." },

    /* Agents */
    { "agent.spawn",              "Spawn an agent process", "Usage: agent.spawn <name> <command> [ws_id]\nSpawns an agent process.\nArguments:\n  <name>   Agent name (string)\n  <command> Command to run (string)\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)" },
    { "agent.list",               "List running agents", "Usage: agent.list\nLists all running agents." },
    { "agent.stop",               "Stop an agent", "Usage: agent.stop <id>\nStops an agent process.\nArguments:\n  <id>  Agent ID (integer)" },
    { "agent.hibernate",          "Hibernate an agent", "Usage: agent.hibernate <id>\nHibernates an agent (kill to save RAM).\nArguments:\n  <id>  Agent ID (integer)" },
    { "agent.resume",             "Resume a hibernated agent", "Usage: agent.resume <id>\nResumes a hibernated agent.\nArguments:\n  <id>  Agent ID (integer)" },
    { "agent.hibernation.status", "Show agent hibernation status", "Usage: agent.hibernation.status\nShows agent hibernation status." },

    /* Capture / messaging */
    { "read-screen",              "Read terminal screen content", "Usage: read-screen [s_id] [ws]\nReads terminal screen content.\nArguments:\n  [s_id]   Surface ID (integer, optional, defaults to current surface)\n  [ws]     Workspace ID (integer, optional, defaults to current workspace)" },
    { "capture-pane",             "Capture pane content", "Usage: capture-pane [s_id] [ws]\nAlias for read-screen.\nArguments:\n  [s_id]   Surface ID (integer, optional, defaults to current surface)\n  [ws]     Workspace ID (integer, optional, defaults to current workspace)" },
    { "display-message",          "Echo a message from the daemon", "Usage: display-message <message>\nEchoes a message back from the daemon.\nArguments:\n  <message> Message to echo (string)" },
    { "pipe-pane",                "Pipe pane output", "Usage: pipe-pane <command> [s_id] [ws]\nPipes pane output to a command.\nArguments:\n  <command> Command to pipe to (string)\n  [s_id]   Surface ID (integer, optional, defaults to current surface)\n  [ws]     Workspace ID (integer, optional, defaults to current workspace)" },

    /* Protocol / control */
    { "events",                   "Drain pending events", "Usage: events\nDrains pending events." },
    { "capabilities",             "List server capabilities", "Usage: capabilities\nLists server capabilities." },
    { "control.attach",           "Attach a control session", "Usage: control.attach\nAttaches a control session." },
    { "refresh-client",           "Subscribe/unsubscribe control notifications", "Usage: refresh-client [-B name:what:format | -B name]\nSubscribes or unsubscribes from control notifications.\nArguments:\n  -B name:what:format  Subscribe with name, what, and format\n  -B name              Unsubscribe by name" },
    { "ping",                     "Check daemon connectivity", "Usage: ping\nChecks daemon connectivity." },
    { "help",                     "List commands", "Usage: help\nLists commands (alias for list-commands)." },
    { "list-commands",            "List all available commands", "Usage: list-commands\nLists all available commands." },

    /* Navigation */
    { "last-pane",                "Focus the last active pane", "Usage: last-pane\nFocuses the last active pane." },
    { "last-window",              "Focus the last active window", "Usage: last-window\nFocuses the last active window." },
    { "next-window",              "Focus the next window", "Usage: next-window\nFocuses the next window." },
    { "previous-window",          "Focus the previous window", "Usage: previous-window\nFocuses the previous window." },
    { "move-surface",             "Move a surface", "Usage: move-surface <from_id> <to_id>\nMoves a surface from one position to another.\nArguments:\n  <from_id> Source surface ID (integer)\n  <to_id>   Target surface ID (integer)" },
    { "reorder-surface",          "Reorder surfaces", "Usage: reorder-surface <from_id> <to_id>\nReorders surfaces from position <from_id> to position <to_id>.\nArguments:\n  <from_id> Source surface ID (integer)\n  <to_id>   Target surface ID (integer)" },
    { "swap-pane",                "Swap two panes", "Usage: swap-pane <id1> <id2>\nSwaps two panes.\nArguments:\n  <id1>  First pane ID (integer)\n  <id2>  Second pane ID (integer)" },
    { "break-pane",               "Break a pane into its own surface", "Usage: break-pane <id> [ws_id]\nBreaks a pane into its own surface.\nArguments:\n  <id>     Pane ID (integer)\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)" },
    { "join-pane",                "Join a pane into another surface", "Usage: join-pane <id> [src_ws] [dst_ws]\nJoins a pane into another surface.\nArguments:\n  <id>     Pane ID (integer)\n  [src_ws] Source workspace ID (integer, optional, defaults to current workspace)\n  [dst_ws] Destination workspace ID (integer, optional, defaults to current workspace)" },
    { "clear-history",            "Clear pane history", "Usage: clear-history <id> [ws_id]\nClears pane history.\nArguments:\n  <id>     Pane ID (integer)\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)" },
    { "respawn-pane",             "Respawn a pane", "Usage: respawn-pane <id> [ws_id]\nRespawns a pane.\nArguments:\n  <id>     Pane ID (integer)\n  [ws_id]  Workspace ID (integer, optional, defaults to current workspace)" },
    { "tree",                     "Print workspace/surface/pane hierarchy", "Usage: tree\nPrints workspace/surface/pane hierarchy." },
    { "ssh",                      "Create an SSH-backed workspace", "Usage: ssh <user@host> [port]\nCreates an SSH-backed workspace.\nArguments:\n  <user@host> SSH connection string (user@host)\n  [port]    SSH port (integer, optional, defaults to 22)" },
    { "wait-for",                 "Wait for a named condition", "Usage: wait-for <name>\nWaits for a named condition.\nArguments:\n  <name>  Condition name (string)" },

    /* Auth */
    { "auth",                     "Show authentication status", "Usage: auth\nShows authentication status." },
    { "auth.login",               "Log in", "Usage: auth.login\nLogs in." },
    { "auth.logout",              "Log out", "Usage: auth.logout\nLogs out." },

    /* Health / metrics */
    { "health.live",              "Check if the daemon is alive", "Usage: health.live\nChecks if the daemon is alive." },
    { "health.ready",             "Check if the daemon is ready", "Usage: health.ready\nChecks if the daemon is ready." },
    { "metrics",                  "Show server metrics", "Usage: metrics\nShows server metrics." },
    { "logs",                     "Show recent daemon logs", "Usage: logs [level] [--follow] [--limit N]\nShows recent daemon logs.\nArguments:\n  [level]     Log level (debug|info|warn|error, optional, defaults to info)\n  --follow    Follow log output (optional)\n  --limit N   Limit number of lines (integer, optional)" },

    /* Browser */
    { "browser.open",             "Open a URL in a browser pane", "Usage: browser.open <url>\nOpens a URL in a browser pane.\nArguments:\n  <url>  URL to open (string)" },
    { "browser.snapshot",         "Get a browser accessibility snapshot", "Usage: browser.snapshot\nGets a browser accessibility snapshot." },
    { "browser.click",            "Click an element in a browser pane", "Usage: browser.click <selector>\nClicks an element in a browser pane.\nArguments:\n  <selector> CSS selector (string)" },
    { "browser.fill",             "Fill a form field", "Usage: browser.fill <selector> <value>\nFills a form field.\nArguments:\n  <selector> CSS selector (string)\n  <value>    Value to fill (string)" },
    { "browser.evaluate",         "Evaluate JavaScript in a browser pane", "Usage: browser.evaluate <js>\nEvaluates JavaScript in a browser pane.\nArguments:\n  <js>  JavaScript code to evaluate (string)" },
    { "browser.screenshot",       "Capture a browser screenshot", "Usage: browser.screenshot [path]\nCaptures a browser screenshot.\nArguments:\n  [path]  File path to save screenshot (string, optional)" },
    { "browser.list",             "List open browsers", "Usage: browser.list\nLists all open browsers." },
    { "browser.import",           "Import a browser session", "Usage: browser.import\nImports a browser session." },

    /* Windows */
    { "window.create",            "Create a window", "Usage: window.create <title>\nCreates a new window.\nArguments:\n  <title>  Window title (string)" },
    { "window.list",              "List windows", "Usage: window.list\nLists all windows." },
    { "window.close",             "Close a window", "Usage: window.close <id>\nCloses a window.\nArguments:\n  <id>  Window ID (integer)" },
    { "window.focus",             "Focus a window", "Usage: window.focus <id>\nFocuses the window with the given ID.\nArguments:\n  <id>  Window ID (integer)" },
    { "window.move-workspace",    "Move a workspace to a window", "Usage: window.move-workspace <win_id> <ws_id>\nMoves a workspace to a window.\nArguments:\n  <win_id>  Window ID (integer)\n  <ws_id>   Workspace ID (integer)" },

    /* SSH sessions */
    { "ssh.session.create",       "Create an SSH session", "Usage: ssh.session.create <host> [user] [key]\nCreates an SSH session.\nArguments:\n  <host>   SSH host (string)\n  [user]   SSH user (string, optional, defaults to current user)\n  [key]    SSH key path (string, optional)" },
    { "ssh.session.list",         "List SSH sessions", "Usage: ssh.session.list\nLists all SSH sessions." },
    { "ssh.session.kill",         "Kill an SSH session", "Usage: ssh.session.kill <id>\nKills an SSH session.\nArguments:\n  <id>  SSH session ID (integer)" },
    { "ssh.session.save",         "Save SSH sessions", "Usage: ssh.session.save [path]\nSaves SSH sessions to a file.\nArguments:\n  [path]  File path (string, optional, defaults to standard location)" },
    { "ssh.session.restore",      "Restore SSH sessions", "Usage: ssh.session.restore [path]\nRestores SSH sessions from a file.\nArguments:\n  [path]  File path (string, optional, defaults to standard location)" },
    { "ssh.list",                 "List active SSH sessions", "Usage: ssh.list\nLists active SSH sessions (alias for ssh.session.list)." },
    { "ssh.connect",              "Connect to a remote host", "Usage: ssh.connect <host> [port] [user] [key]\nConnects to a remote host via SSH.\nArguments:\n  <host>   Remote host (string)\n  [port]   SSH port (integer, optional, defaults to 22)\n  [user]   SSH user (string, optional)\n  [key]    SSH key path (string, optional)" },
    { "ssh.disconnect",           "Disconnect an SSH session", "Usage: ssh.disconnect <session_id>\nDisconnects an SSH session.\nArguments:\n  <session_id> SSH session ID (integer)" },

    /* Feed panels */
    { "feed.panel.create",        "Create a feed panel", "Usage: feed.panel.create <title> [filter]\nCreates a feed panel.\nArguments:\n  <title>  Feed panel title (string)\n  [filter]  Filter string (string, optional)" },
    { "feed.panel.list",          "List feed panels", "Usage: feed.panel.list\nLists all feed panels." },
    { "feed.panel.close",         "Close a feed panel", "Usage: feed.panel.close <id>\nCloses a feed panel.\nArguments:\n  <id>  Feed panel ID (integer)" },
    { "feed.entry.add",           "Add a feed entry", "Usage: feed.entry.add <panel_id> <text>\nAdds a feed entry to a panel.\nArguments:\n  <panel_id> Feed panel ID (integer)\n  <text>     Entry text (string)" },
    { "feed.entry.list",          "List feed entries", "Usage: feed.entry.list <panel_id>\nLists feed entries in a panel.\nArguments:\n  <panel_id> Feed panel ID (integer)" },
    { "feed.panel.clear",         "Clear a feed panel", "Usage: feed.panel.clear <panel_id>\nClears a feed panel.\nArguments:\n  <panel_id> Feed panel ID (integer)" },

    /* Search */
    { "search.start",             "Start a terminal search", "Usage: search.start\nStarts a terminal search." },
    { "search.next",              "Find next search match", "Usage: search.next\nFinds next search match." },
    { "search.prev",              "Find previous search match", "Usage: search.prev\nFinds previous search match." },
    { "search.cancel",            "Cancel a search", "Usage: search.cancel\nCancels a search." },
    { "search.status",            "Show search status", "Usage: search.status\nShows search status." },

    /* Calendar / email / weather */
    { "calendar.import",          "Import a calendar", "Usage: calendar.import\nImports a calendar." },
    { "calendar.today",           "Show today's calendar events", "Usage: calendar.today\nShows today's calendar events." },
    { "calendar.upcoming",        "Show upcoming calendar events", "Usage: calendar.upcoming\nShows upcoming calendar events." },
    { "email.import",             "Import email", "Usage: email.import\nImports email." },
    { "email.list",               "List email", "Usage: email.list\nLists email." },
    { "email.search",             "Search email", "Usage: email.search\nSearches email." },
    { "weather.get",              "Get weather", "Usage: weather.get\nGets weather." },
    { "weather.set_location",     "Set weather location", "Usage: weather.set_location <location>\nSets weather location.\nArguments:\n  <location>  Location (string)" },
    { "weather.refresh",          "Refresh weather", "Usage: weather.refresh\nRefreshes weather." },

    /* Clipboard */
    { "clipboard.copy",           "Copy to clipboard", "Usage: clipboard.copy\nCopies selected text to clipboard." },
    { "clipboard.paste",          "Paste from clipboard", "Usage: clipboard.paste\nPastes text from clipboard." },
    { "clipboard.list",           "List clipboard history", "Usage: clipboard.list\nLists clipboard history." },
    { "clipboard.clear",          "Clear clipboard history", "Usage: clipboard.clear\nClears clipboard history." },

    /* Templates */
    { "template.list",            "List templates", "Usage: template.list\nLists all templates." },
    { "template.create",          "Create a template", "Usage: template.create <name>\nCreates a new template.\nArguments:\n  <name>  Template name (string)" },
    { "template.save",            "Save a template", "Usage: template.save <name> <path>\nSaves a template to a file.\nArguments:\n  <name>  Template name (string)\n  <path>  File path (string)" },
    { "template.delete",          "Delete a template", "Usage: template.delete <name>\nDeletes a template.\nArguments:\n  <name>  Template name (string)" },

    /* Profiling */
    { "profile.status",           "Show profile status", "Usage: profile.status\nShows profile status." },
    { "profile.start",            "Start profiling", "Usage: profile.start\nStarts profiling." },
    { "profile.stop",             "Stop profiling", "Usage: profile.stop\nStops profiling." },
    { "profile.list",             "List profiles", "Usage: profile.list\nLists all profiles." },

    /* Cloud VMs */
    { "cloud.vms.list",           "List cloud VMs", "Usage: cloud.vms\nLists all cloud VMs." },
    { "cloud.vms.create",         "Create a cloud VM", "Usage: cloud.vms.create <name> <type> [size] [region]\nCreates a cloud VM.\nArguments:\n  <name>   VM name (string)\n  <type>   VM type (string)\n  [size]   VM size (string, optional)\n  [region] Region (string, optional)" },
    { "cloud.vms.destroy",        "Destroy a cloud VM", "Usage: cloud.vms.destroy <id>\nDestroys a cloud VM.\nArguments:\n  <id>  VM ID (integer)" },
    { "cloud.vms.ssh",            "SSH to a cloud VM", "Usage: cloud.vms.ssh <id>\nSSHs to a cloud VM.\nArguments:\n  <id>  VM ID (integer)" },

    /* Companion */
    { "companion.register",       "Register a companion device", "Usage: companion.register <name> <type> <id>\nRegisters a companion device.\nArguments:\n  <name>  Device name (string)\n  <type>  Device type (string)\n  <id>    Device ID (string)" },
    { "companion.status",         "Show companion status", "Usage: companion.status [id]\nShows companion status.\nArguments:\n  [id]  Companion ID (string, optional, omits to show all)" },
    { "companion.notify",         "Notify a companion device", "Usage: companion.notify <id> <message>\nNotifies a companion device.\nArguments:\n  <id>    Companion ID (string)\n  <message> Message to send (string)" },
    { "companion.unregister",     "Unregister a companion device", "Usage: companion.unregister <id>\nUnregisters a companion device.\nArguments:\n  <id>  Companion ID (string)" },

    /* Teams */
    { "team.create",              "Create an agent team", "Usage: team.create <name>\nCreates an agent team.\nArguments:\n  <name>  Team name (string)" },
    { "team.add",                 "Add an agent to a team", "Usage: team.add <name> <agent_id>\nAdds an agent to a team.\nArguments:\n  <name>    Team name (string)\n  <agent_id> Agent ID (integer)" },
    { "team.remove",              "Remove an agent from a team", "Usage: team.remove <name> <agent_id>\nRemoves an agent from a team.\nArguments:\n  <name>    Team name (string)\n  <agent_id> Agent ID (integer)" },
    { "team.list",                "List agent teams", "Usage: team.list\nLists all agent teams." },
    { "team.dispatch",            "Dispatch work to a team", "Usage: team.dispatch <name> <command>\nDispatches work to a team.\nArguments:\n  <name>    Team name (string)\n  <command> Command to run (string)" },
    { "team.delete",              "Delete an agent team", "Usage: team.delete <name>\nDeletes an agent team.\nArguments:\n  <name>  Team name (string)" },

    /* File explorer */
    { "file_explorer.navigate",   "Navigate the file explorer", "Usage: file_explorer.navigate <path>\nNavigates the file explorer to a path.\nArguments:\n  <path>  File system path (string)" },
    { "file_explorer.list",       "List file explorer entries", "Usage: file_explorer.list\nLists file explorer entries." },
    { "file_explorer.open",       "Open a file explorer path", "Usage: file_explorer.open <path>\nOpens a file explorer path.\nArguments:\n  <path>  File system path (string)" },
    { "file_explorer.refresh",    "Refresh the file explorer", "Usage: file_explorer.refresh\nRefreshes the file explorer." },
    { "file_explorer.filter",     "Filter file explorer entries", "Usage: file_explorer.filter <pattern>\nFilters file explorer entries.\nArguments:\n  <pattern> Filter pattern (string)" },
    { "file_explorer.sort",       "Sort file explorer entries", "Usage: file_explorer.sort <mode>\nSorts file explorer entries.\nArguments:\n  <mode>  Sort mode: 0=name, 1=size, 2=time" },
    { "file_explorer.open_file",  "Open a file", "Usage: file_explorer.open_file <path>\nOpens a file.\nArguments:\n  <path>  File system path (string)" },
    { "file_explorer.create_dir", "Create a directory", "Usage: file_explorer.create_dir <path>\nCreates a directory.\nArguments:\n  <path>  File system path (string)" },
    { "file_explorer.delete",     "Delete a file or directory", "Usage: file_explorer.delete <path>\nDeletes a file or directory.\nArguments:\n  <path>  File system path (string)" },
    { "file_explorer.rename",     "Rename a file or directory", "Usage: file_explorer.rename <src> <dst>\nRenames a file or directory.\nArguments:\n  <src>   Source path (string)\n  <dst>   Destination path (string)" },
    { "file_explorer.search",     "Search files", "Usage: file_explorer.search <pattern>\nSearches files.\nArguments:\n  <pattern> Search pattern (string)" },
    { "file_explorer.close",      "Close the file explorer", "Usage: file_explorer.close\nCloses the file explorer." },

    /* Hooks / focus / naming */
    { "hooks.add",                "Register a hook script", "Usage: hooks.add <event> <script>\nRegisters a hook script for an event.\nArguments:\n  <event>   Event name (string)\n  <script>  Script path (string)" },
    { "hooks.list",               "List registered hooks", "Usage: hooks.list\nLists all registered hooks." },
    { "hooks.remove",             "Unregister a hook", "Usage: hooks.remove <event> <script>\nUnregisters a hook script for an event.\nArguments:\n  <event>   Event name (string)\n  <script>  Script path (string)" },
    { "focus.history",            "Show focus history", "Usage: focus.history [count]\nShows focus history.\nArguments:\n  [count]  Number of history entries to show (integer, optional, defaults to 10)" },
    { "naming.suggest",           "Suggest workspace names", "Usage: naming.suggest <dir>\nSuggests workspace names for a directory.\nArguments:\n  <dir>  Directory path (string)" },
};

const lmux_cmd_entry *lmux_registry_all(size_t *count) {
    if (count) {
        *count = sizeof lmux_cmd_table / sizeof lmux_cmd_table[0];
    }
    return lmux_cmd_table;
}

const lmux_cmd_entry *lmux_registry_get(const char *name) {
    if (!name) return NULL;
    const size_t n = sizeof lmux_cmd_table / sizeof lmux_cmd_table[0];
    for (size_t i = 0; i < n; i++) {
        if (strcmp(lmux_cmd_table[i].name, name) == 0) {
            return &lmux_cmd_table[i];
        }
    }
    return NULL;
}
