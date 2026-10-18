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
    { "workspace.create",         "Create a new workspace" },
    { "workspace.list",           "List all workspaces" },
    { "workspace.close",          "Close a workspace" },
    { "workspace.select",         "Focus a workspace" },
    { "workspace.current",        "Show the focused workspace" },
    { "workspace.rename",         "Rename a workspace" },
    { "workspace.reorder",        "Reorder workspaces" },
    { "workspace.refresh",        "Refresh workspace git/branch/port metadata" },
    { "workspace.env",            "Set workspace environment variables" },
    { "workspace.group.create",   "Create a workspace group" },
    { "workspace.group.list",     "List workspace groups" },
    { "workspace.group.add",      "Add a workspace to a group" },
    { "workspace.group.remove",   "Remove a workspace from a group" },

    /* Surfaces (horizontal tabs) */
    { "surface.create",           "Create a surface (horizontal tab)" },
    { "surface.list",             "List surfaces" },
    { "surface.close",            "Close a surface" },
    { "surface.split",            "Split a surface into panes" },
    { "surface.focus",            "Focus a surface" },
    { "surface.rename",           "Rename a surface" },
    { "surface.send_text",        "Send text to a surface" },
    { "surface.send_key",         "Send a key to a surface" },
    { "surface.set_sync_panes",   "Toggle synchronized panes on a surface" },
    { "surface.set_window_size",  "Set surface window size" },
    { "surface.canvas.enable",    "Enable free-form canvas layout" },
    { "surface.canvas.disable",   "Disable canvas layout" },
    { "surface.canvas.move_pane", "Move a pane on the canvas" },
    { "surface.canvas.resize_pane", "Resize a pane on the canvas" },
    { "surface.canvas.set_z",     "Set canvas pane z-order" },
    { "surface.canvas.get_layout", "Get canvas layout" },
    { "surface.canvas.set_layout", "Set canvas layout" },

    /* Panes */
    { "pane.list",                "List panes" },
    { "pane.focus",               "Focus a pane" },
    { "pane.resize",              "Resize a pane" },
    { "pane.focus_dir",           "Move pane focus in a direction" },
    { "pane.set_remain_on_exit",  "Set remain-on-exit for a pane" },
    { "pane.get_remain_on_exit",  "Get remain-on-exit for a pane" },
    { "pane.copy_mode.enter",     "Enter pane copy mode" },
    { "pane.copy_mode.exit",      "Exit pane copy mode" },
    { "pane.copy_mode.move",      "Move cursor in copy mode" },
    { "pane.copy_mode.select_start", "Begin copy-mode selection" },
    { "pane.copy_mode.select_end",   "End copy-mode selection" },
    { "pane.copy_mode.yank",      "Yank copy-mode selection" },
    { "pane.copy_mode.paste",     "Paste from copy mode" },

    /* Paste buffers */
    { "buffer.set",               "Set a paste buffer" },
    { "buffer.get",               "Get a paste buffer" },
    { "buffer.list",              "List paste buffers" },
    { "buffer.delete",            "Delete a paste buffer" },
    { "buffer.save",              "Save a buffer to a file" },
    { "buffer.load",              "Load a buffer from a file" },
    { "buffer.capture_pane",      "Capture pane content into a buffer" },
    { "buffer.limit",             "Set or get the paste buffer limit" },
    { "buffer.count",             "Count paste buffers" },

    /* Notifications */
    { "notification.create",      "Create a notification" },
    { "notification.list",        "List notifications" },
    { "notification.clear",       "Clear notifications" },
    { "notification.mark_read",   "Mark a notification read" },
    { "notification.ring.add",    "Add a notification ring to a pane" },
    { "notification.ring.clear",  "Clear a notification ring" },
    { "notification.ring.list",   "List notification rings" },
    { "notification.hooks.add",   "Add a notification hook" },
    { "notification.hooks.remove","Remove a notification hook" },
    { "notification.hooks.list",  "List notification hooks" },
    { "notification.hook.list",   "List notification hooks" },
    { "notification.hook.remove", "Remove a notification hook" },

    /* Session / snapshot */
    { "snapshot.save",            "Save a session snapshot" },
    { "snapshot.load",            "Load a session snapshot" },
    { "session.save",             "Save the session to disk" },
    { "session.restore",          "Restore the session from disk" },

    /* Configuration */
    { "config.get",               "Get configuration values" },
    { "config.set",               "Set a configuration value" },
    { "reload-config",            "Reload configuration" },

    /* Agents */
    { "agent.spawn",              "Spawn an agent process" },
    { "agent.list",               "List running agents" },
    { "agent.stop",               "Stop an agent" },
    { "agent.hibernate",          "Hibernate an agent" },
    { "agent.resume",             "Resume a hibernated agent" },
    { "agent.hibernation.status", "Show agent hibernation status" },

    /* Capture / messaging */
    { "read-screen",              "Read terminal screen content" },
    { "capture-pane",             "Capture pane content" },
    { "display-message",          "Echo a message from the daemon" },
    { "pipe-pane",                "Pipe pane output" },

    /* Protocol / control */
    { "events",                   "Drain pending events" },
    { "capabilities",             "List server capabilities" },
    { "control.attach",           "Attach a control session" },
    { "refresh-client",           "Subscribe/unsubscribe control notifications" },
    { "ping",                     "Check daemon connectivity" },
    { "help",                     "List commands" },
    { "list-commands",            "List all available commands" },

    /* Navigation */
    { "last-pane",                "Focus the last active pane" },
    { "last-window",              "Focus the last active window" },
    { "next-window",              "Focus the next window" },
    { "previous-window",          "Focus the previous window" },
    { "move-surface",             "Move a surface" },
    { "reorder-surface",          "Reorder surfaces" },
    { "swap-pane",                "Swap two panes" },
    { "break-pane",               "Break a pane into its own surface" },
    { "join-pane",                "Join a pane into another surface" },
    { "clear-history",            "Clear pane history" },
    { "respawn-pane",             "Respawn a pane" },
    { "tree",                     "Print workspace/surface/pane hierarchy" },
    { "ssh",                      "Create an SSH-backed workspace" },
    { "wait-for",                 "Wait for a named condition" },

    /* Auth */
    { "auth",                     "Show authentication status" },
    { "auth.login",               "Log in" },
    { "auth.logout",              "Log out" },

    /* Health / metrics */
    { "health.live",              "Check if the daemon is alive" },
    { "health.ready",             "Check if the daemon is ready" },
    { "metrics",                  "Show server metrics" },
    { "logs",                     "Show recent daemon logs" },

    /* Browser */
    { "browser.open",             "Open a URL in a browser pane" },
    { "browser.snapshot",         "Get a browser accessibility snapshot" },
    { "browser.click",            "Click an element in a browser pane" },
    { "browser.fill",             "Fill a form field" },
    { "browser.evaluate",         "Evaluate JavaScript in a browser pane" },
    { "browser.screenshot",       "Capture a browser screenshot" },
    { "browser.list",             "List open browsers" },
    { "browser.import",           "Import a browser session" },

    /* Windows */
    { "window.create",            "Create a window" },
    { "window.list",              "List windows" },
    { "window.close",             "Close a window" },
    { "window.focus",             "Focus a window" },
    { "window.move-workspace",    "Move a workspace to a window" },

    /* SSH sessions */
    { "ssh.session.create",       "Create an SSH session" },
    { "ssh.session.list",         "List SSH sessions" },
    { "ssh.session.kill",         "Kill an SSH session" },
    { "ssh.session.save",         "Save SSH sessions" },
    { "ssh.session.restore",      "Restore SSH sessions" },
    { "ssh.list",                 "List active SSH sessions" },
    { "ssh.connect",              "Connect to a remote host" },
    { "ssh.disconnect",           "Disconnect an SSH session" },

    /* Feed panels */
    { "feed.panel.create",        "Create a feed panel" },
    { "feed.panel.list",          "List feed panels" },
    { "feed.panel.close",         "Close a feed panel" },
    { "feed.entry.add",           "Add a feed entry" },
    { "feed.entry.list",          "List feed entries" },
    { "feed.panel.clear",         "Clear a feed panel" },

    /* Search */
    { "search.start",             "Start a terminal search" },
    { "search.next",              "Find next search match" },
    { "search.prev",              "Find previous search match" },
    { "search.cancel",            "Cancel a search" },
    { "search.status",            "Show search status" },

    /* Calendar / email / weather */
    { "calendar.import",          "Import a calendar" },
    { "calendar.today",           "Show today's calendar events" },
    { "calendar.upcoming",        "Show upcoming calendar events" },
    { "email.import",             "Import email" },
    { "email.list",               "List email" },
    { "email.search",             "Search email" },
    { "weather.get",              "Get weather" },
    { "weather.set_location",     "Set weather location" },
    { "weather.refresh",          "Refresh weather" },

    /* Clipboard */
    { "clipboard.copy",           "Copy to clipboard" },
    { "clipboard.paste",          "Paste from clipboard" },
    { "clipboard.list",           "List clipboard history" },
    { "clipboard.clear",          "Clear clipboard history" },

    /* Templates */
    { "template.list",            "List templates" },
    { "template.create",          "Create a template" },
    { "template.save",            "Save a template" },
    { "template.delete",          "Delete a template" },

    /* Profiling */
    { "profile.status",           "Show profile status" },
    { "profile.start",            "Start profiling" },
    { "profile.stop",             "Stop profiling" },
    { "profile.list",             "List profiles" },

    /* Cloud VMs */
    { "cloud.vms.list",           "List cloud VMs" },
    { "cloud.vms.create",         "Create a cloud VM" },
    { "cloud.vms.destroy",        "Destroy a cloud VM" },
    { "cloud.vms.ssh",            "SSH to a cloud VM" },

    /* Companion */
    { "companion.register",       "Register a companion device" },
    { "companion.status",         "Show companion status" },
    { "companion.notify",         "Notify a companion device" },
    { "companion.unregister",     "Unregister a companion device" },

    /* Teams */
    { "team.create",              "Create an agent team" },
    { "team.add",                 "Add an agent to a team" },
    { "team.remove",              "Remove an agent from a team" },
    { "team.list",                "List agent teams" },
    { "team.dispatch",            "Dispatch work to a team" },
    { "team.delete",              "Delete an agent team" },

    /* File explorer */
    { "file_explorer.navigate",   "Navigate the file explorer" },
    { "file_explorer.list",       "List file explorer entries" },
    { "file_explorer.open",       "Open a file explorer path" },
    { "file_explorer.refresh",    "Refresh the file explorer" },
    { "file_explorer.filter",     "Filter file explorer entries" },
    { "file_explorer.sort",       "Sort file explorer entries" },
    { "file_explorer.open_file",  "Open a file" },
    { "file_explorer.create_dir", "Create a directory" },
    { "file_explorer.delete",     "Delete a file or directory" },
    { "file_explorer.rename",     "Rename a file or directory" },
    { "file_explorer.search",     "Search files" },
    { "file_explorer.close",      "Close the file explorer" },

    /* Hooks / focus / naming */
    { "hooks.add",                "Register a hook script" },
    { "hooks.list",               "List registered hooks" },
    { "hooks.remove",             "Unregister a hook" },
    { "focus.history",            "Show focus history" },
    { "naming.suggest",           "Suggest workspace names" },
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
