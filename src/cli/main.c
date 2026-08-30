/*
 * main.c - lmux CLI entry point.
 *
 * The lmux CLI connects to a running lmux daemon/app via a Unix
 * domain socket, sends a JSON command, and prints the response.
 *
 * In daemon mode (lmux daemon), it starts a headless lmux instance
 * with a socket server — useful for testing and scripting without
 * the GTK UI.
 *
 * Usage:
 *   lmux <command> [args...]
 *   lmux --socket <path> <command> [args...]
 *   lmux --json <command> [args...]    # raw JSON output
 *   lmux daemon                         # start headless daemon
 *   lmux help                           # show help
 *   lmux version                        # show version
 */
#define _POSIX_C_SOURCE 200809L


#include "lmux.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <errno.h>
#include <signal.h>
#include <sys/wait.h>

/* ------------------------------------------------------------------ */
/* Graceful shutdown for daemon mode                                    */
/* ------------------------------------------------------------------ */

static volatile sig_atomic_t daemon_should_exit = 0;
static lmux_app *daemon_app_global = NULL;

/* Async-signal-safe SIGCHLD handler: reap terminated children immediately. */
static void sigchld_handler(int sig) {
    (void)sig;
    int saved_errno = errno;
    while (waitpid(-1, NULL, WNOHANG) > 0) {
        /* reap all terminated children */
    }
    errno = saved_errno;
}

/* SIGTERM/SIGINT handler: just set the exit flag.
 * The actual cleanup (snapshot save, app quit) runs in the
 * main loop to avoid calling async-unsafe functions. */
static void handle_signal(int sig) {
    (void)sig;
    daemon_should_exit = 1;
}
/* ------------------------------------------------------------------ */
/* Socket client helpers                                                */
/* ------------------------------------------------------------------ */

static int connect_socket(const char *path) {
    struct sockaddr_un addr;
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        fprintf(stderr, "lmux: socket() failed: %s\n", strerror(errno));
        return -1;
    }
    memset(&addr, 0, sizeof addr);
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, path, sizeof addr.sun_path - 1);
    if (connect(fd, (struct sockaddr *)&addr, sizeof addr) < 0) {
        fprintf(stderr, "lmux: connect(%s) failed: %s\n",
                path, strerror(errno));
        close(fd);
        return -1;
    }
    return fd;
}

static char *send_request(int fd, const char *json_request) {
    /* Send request. */
    size_t len = strlen(json_request);
    if (write(fd, json_request, len) != (ssize_t)len) {
        fprintf(stderr, "lmux: write failed: %s\n", strerror(errno));
        return NULL;
    }
    /* Read response — dynamically allocate to handle arbitrary response sizes. */
    size_t cap = 65536;
    size_t n = 0;
    char *buf = malloc(cap);
    if (!buf) {
        fprintf(stderr, "lmux: out of memory\n");
        return NULL;
    }
    ssize_t r;
    while ((r = read(fd, buf + n, cap - n - 1)) > 0) {
        n += (size_t)r;
        /* Grow buffer if needed */
        if (n >= cap - 1) {
            cap *= 2;
            char *new_buf = realloc(buf, cap);
            if (!new_buf) {
                free(buf);
                fprintf(stderr, "lmux: out of memory\n");
                return NULL;
            }
            buf = new_buf;
        }
    }
    if (n == 0 && r == 0) {
        fprintf(stderr, "lmux: read failed: connection closed\n");
        free(buf);
        return NULL;
    }
    if (r < 0 && n == 0) {
        fprintf(stderr, "lmux: read failed: %s\n", strerror(errno));
        free(buf);
        return NULL;
    }
    buf[n] = 0;
    return buf;
}

/* ------------------------------------------------------------------ */
/* JSON builder (minimal, no deps)                                     */
/* ------------------------------------------------------------------ */


/* ------------------------------------------------------------------ */
/* Command-to-JSON translation                                         */
/* ------------------------------------------------------------------ */
/* Escape a string for safe inclusion in a JSON string value.
 * Writes to `out` (capacity `cap`), returns bytes written (excluding NUL). */
static size_t json_escape(const char *in, char *out, size_t cap) {
    size_t j = 0;
    for (const char *p = in; *p && j + 1 < cap; p++) {
        char c = *p;
        if (c == '"' || c == '\\') {
            if (j + 2 >= cap) break;
            out[j++] = '\\';
            out[j++] = c;
        } else if ((unsigned char)c < 0x20) {
            if (j + 6 >= cap) break;
            j += (size_t)snprintf(out + j, cap - j, "\\u%04x", (unsigned char)c);
        } else {
            out[j++] = c;
        }
    }
    out[j] = 0;
    return j;
}


/* Build a JSON command string from argc/argv.
 * Called with argv starting at the command name.
 * Returns a malloc'd string. */
static char *build_json_command(int argc, char **argv) {
    if (argc < 1) return NULL;

    /* Normalize command name: translate hyphens to underscores */
    char cmd_normalized[128];
    snprintf(cmd_normalized, sizeof cmd_normalized, "%s", argv[0]);
    for (char *p = cmd_normalized; *p; p++) {
        if (*p == '-') *p = '_';
    }
    const char *cmd = cmd_normalized;
    size_t args_len = 0;
    char args_buf[4096];
    char _esc[4096];  /* scratch buffer for json_escape */
    args_buf[0] = 0;

    /* snapshot.save [path] */
    if (strcmp(cmd, "snapshot.save") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"path\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        }
    }
    /* snapshot.load [path] */
    else if (strcmp(cmd, "snapshot.load") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"path\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        }
    }

    /* move-surface <id> <target_ws> [source_ws] */
    else if (strcmp(cmd, "move-surface") == 0 || strcmp(cmd, "move_surface") == 0) {
        if (argc >= 3) {
            args_len = snprintf(args_buf, sizeof args_buf,
                                "\"id\":\"%s\",\"target_workspace_id\":\"%s\"", argv[1], argv[2]);
            if (argc >= 4) {
                args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"workspace_id\":\"%s\"", (json_escape(argv[3], _esc, sizeof _esc), _esc));
            }
        }
    }

    /* surface.rename <id> <title> [ws_id] */
    else if (strcmp(cmd, "surface.rename") == 0) {
        if (argc >= 3) {
            args_len = snprintf(args_buf, sizeof args_buf,
                                "\"id\":\"%s\",\"title\":\"%s\"", argv[1], argv[2]);
            if (argc >= 4) {
                args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"workspace_id\":\"%s\"", (json_escape(argv[3], _esc, sizeof _esc), _esc));
            }
        }
    }

    /* events */
    if (strcmp(cmd, "events") == 0) {
        /* args: {} — args_len stays 0 */
    }
    /* pane.resize <direction> [cols] [rows] [surface_id] [workspace_id] */
    else if (strcmp(cmd, "pane.resize") == 0 && argc >= 2) {
        args_len = snprintf(args_buf, sizeof args_buf, "\"direction\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        if (argc >= 3) {
            args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"cols\":\"%s\"", (json_escape(argv[2], _esc, sizeof _esc), _esc));
        }
        if (argc >= 4) {
            args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"rows\":\"%s\"", (json_escape(argv[3], _esc, sizeof _esc), _esc));
        }
        if (argc >= 5) {
            args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"surface_id\":\"%s\"", (json_escape(argv[4], _esc, sizeof _esc), _esc));
        }
        if (argc >= 6) {
            args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"workspace_id\":\"%s\"", (json_escape(argv[5], _esc, sizeof _esc), _esc));
        }
    }
    /* pane.focus_dir <dx> [dy] */
    else if (strcmp(cmd, "pane.focus_dir") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"dx\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
            if (argc >= 3) {
                args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"dy\":\"%s\"", (json_escape(argv[2], _esc, sizeof _esc), _esc));
            }
        }
    }
    /* workspace.refresh [workspace_id] */
    else if (strcmp(cmd, "workspace.refresh") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"workspace_id\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        }
    }
    /* read-screen [surface_id] [workspace_id] */
    else if (strcmp(cmd, "read_screen") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"surface_id\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
            if (argc >= 3) {
                args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"workspace_id\":\"%s\"", (json_escape(argv[2], _esc, sizeof _esc), _esc));
            }
        }
    }
    /* capture-pane [surface_id] [workspace_id] — alias for read-screen */
    else if (strcmp(cmd, "capture_pane") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"surface_id\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
            if (argc >= 3) {
                args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"workspace_id\":\"%s\"", (json_escape(argv[2], _esc, sizeof _esc), _esc));
            }
        }
    }
    /* capabilities */
    else if (strcmp(cmd, "capabilities") == 0) {
        /* args: {} — args_len stays 0 */
    }

    /* Translate common positional args to JSON params. */
    /* workspace.reorder <from> <to> */
    if (strcmp(cmd, "workspace.reorder") == 0 && argc >= 3) {
        args_len = snprintf(args_buf, sizeof args_buf,
                            "\"from_index\":\"%s\",\"to_index\":\"%s\"", argv[1], argv[2]);
    }
    /* surface.focus <id> [workspace_id] */
    else if (strcmp(cmd, "surface.focus") == 0 && argc >= 2) {
        args_len = snprintf(args_buf, sizeof args_buf, "\"id\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        if (argc >= 3) {
            args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"workspace_id\":\"%s\"", (json_escape(argv[2], _esc, sizeof _esc), _esc));
        }
    }
    /* surface.send-key <key> [surface_id] [workspace_id] */
    else if (strcmp(cmd, "surface.send_key") == 0 && argc >= 2) {
        args_len = snprintf(args_buf, sizeof args_buf, "\"text\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        if (argc >= 3) {
            args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"surface_id\":\"%s\"", (json_escape(argv[2], _esc, sizeof _esc), _esc));
        }
        if (argc >= 4) {
            args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"workspace_id\":\"%s\"", (json_escape(argv[3], _esc, sizeof _esc), _esc));
        }
    }
    /* notification.clear */
    else if (strcmp(cmd, "notification.clear") == 0) {
        /* args: {} — args_len stays 0 */
    }
    /* notification.mark-read [seq] */
    else if (strcmp(cmd, "notification.mark_read") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"seq\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        }
    }
    /* workspace.create <title> */
    else if (strcmp(cmd, "workspace.create") == 0 && argc >= 2) {
        args_len = snprintf(args_buf, sizeof args_buf, "\"title\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
    }
    /* workspace close <id> */
    else if (strcmp(cmd, "workspace.close") == 0 && argc >= 2) {
        args_len = snprintf(args_buf, sizeof args_buf, "\"id\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
    }
    /* workspace select <id> */
    else if (strcmp(cmd, "workspace.select") == 0 && argc >= 2) {
        args_len = snprintf(args_buf, sizeof args_buf, "\"id\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
    }
    /* workspace rename <id> <title> */
    else if (strcmp(cmd, "workspace.rename") == 0 && argc >= 3) {
        args_len = snprintf(args_buf, sizeof args_buf,
                            "\"id\":\"%s\",\"title\":\"%s\"", argv[1], argv[2]);
    }
    /* surface.create [workspace_id] [title] */
    else if (strcmp(cmd, "surface.create") == 0) {
        if (argc >= 2) args_len = snprintf(args_buf, sizeof args_buf, "\"workspace_id\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        if (argc >= 3) {
            args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"title\":\"%s\"", (json_escape(argv[2], _esc, sizeof _esc), _esc));
        }
    }
    /* surface.close <surface_id> [workspace_id] */
    else if (strcmp(cmd, "surface.close") == 0 && argc >= 2) {
        args_len = snprintf(args_buf, sizeof args_buf, "\"id\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        if (argc >= 3) {
            args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"workspace_id\":\"%s\"", (json_escape(argv[2], _esc, sizeof _esc), _esc));
        }
    }
    /* surface.split <direction> [surface_id] [workspace_id] */
    else if (strcmp(cmd, "surface.split") == 0 && argc >= 2) {
        args_len = snprintf(args_buf, sizeof args_buf, "\"direction\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        if (argc >= 3) {
            args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"surface_id\":\"%s\"", (json_escape(argv[2], _esc, sizeof _esc), _esc));
        }
        if (argc >= 4) {
            args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"workspace_id\":\"%s\"", (json_escape(argv[3], _esc, sizeof _esc), _esc));
        }
    }
    /* pane.focus <pane_id> [workspace_id] */
    else if (strcmp(cmd, "pane.focus") == 0 && argc >= 2) {
        args_len = snprintf(args_buf, sizeof args_buf, "\"id\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        if (argc >= 3) {
            args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"workspace_id\":\"%s\"", (json_escape(argv[2], _esc, sizeof _esc), _esc));
        }
    }
    /* surface.send_text <text> [surface_id] [workspace_id] */
    else if (strcmp(cmd, "surface.send_text") == 0 && argc >= 2) {
        args_len = snprintf(args_buf, sizeof args_buf, "\"text\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        if (argc >= 3) {
            args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"surface_id\":\"%s\"", (json_escape(argv[2], _esc, sizeof _esc), _esc));
        }
        if (argc >= 4) {
            args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"workspace_id\":\"%s\"", (json_escape(argv[3], _esc, sizeof _esc), _esc));
        }
    }
    /* notification.create <text> [waiting] [workspace_id] */
    else if (strcmp(cmd, "notification.create") == 0 && argc >= 2) {
        args_len = snprintf(args_buf, sizeof args_buf, "\"text\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        if (argc >= 3) {
            args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len,
                ",\"waiting\":%s", strcmp(argv[2], "true") == 0 ? "true" : "false");
        }
        if (argc >= 4) {
            args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"workspace_id\":\"%s\"", (json_escape(argv[3], _esc, sizeof _esc), _esc));
        }
    }
    /* events */
    else if (strcmp(cmd, "events") == 0) {
        /* args: {} — stays 0-length */
    }
    /* capabilities */
    else if (strcmp(cmd, "capabilities") == 0) {
        /* args: {} — stays 0-length */
    }
    /* workspace.refresh */
    else if (strcmp(cmd, "workspace.refresh") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"workspace_id\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        }
    }
    /* pane.resize */
    else if (strcmp(cmd, "pane.resize") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"direction\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        }
        if (argc >= 3) {
            args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"surface_id\":\"%s\"", (json_escape(argv[2], _esc, sizeof _esc), _esc));
        }
        if (argc >= 4) {
            args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"workspace_id\":\"%s\"", (json_escape(argv[3], _esc, sizeof _esc), _esc));
        }
    }
    /* pane.focus_dir */
    else if (strcmp(cmd, "pane.focus_dir") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"dx\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        }
        if (argc >= 3) {
            args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"dy\":\"%s\"", (json_escape(argv[2], _esc, sizeof _esc), _esc));
        }
    }
    /* reload-config */
    else if (strcmp(cmd, "reload_config") == 0) {
        /* args: {} — args_len stays 0 */
    }
    /* workspace.env [key] [value] */
    else if (strcmp(cmd, "workspace.env") == 0 || strcmp(cmd, "workspace_env") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"key\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
            if (argc >= 3) {
                args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"value\":\"%s\"", (json_escape(argv[2], _esc, sizeof _esc), _esc));
            }
        }
    }
    /* pipe-pane <id> <command> */
    else if (strcmp(cmd, "pipe_pane") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"id\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
            if (argc >= 3) {
                args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"command\":\"%s\"", (json_escape(argv[2], _esc, sizeof _esc), _esc));
            }
        }
    }
    /* display-message <message> */
    else if (strcmp(cmd, "display_message") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"message\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        }
    }
    /* wait-for <name> <action> */
    else if (strcmp(cmd, "wait_for") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"name\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
            if (argc >= 3) {
                args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"action\":\"%s\"", (json_escape(argv[2], _esc, sizeof _esc), _esc));
            }
        }
    }
    /* auth */
    else if (strcmp(cmd, "auth") == 0 ||
             strcmp(cmd, "auth_status") == 0 ||
             strcmp(cmd, "auth_login") == 0 ||
             strcmp(cmd, "auth_logout") == 0) {
        /* args: {} — args_len stays 0 */
    }
    /* clear-history — no args */
    else if (strcmp(cmd, "clear_history") == 0) {
        /* args: {} — args_len stays 0 */
    }
    /* respawn-pane [id] */
    else if (strcmp(cmd, "respawn-pane") == 0 || strcmp(cmd, "respawn_pane") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"id\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        }
    }
    /* reorder-surface <id> <index> */
    else if (strcmp(cmd, "reorder-surface") == 0 || strcmp(cmd, "reorder_surface") == 0) {
        if (argc >= 3) {
            args_len = snprintf(args_buf, sizeof args_buf,
                                "\"id\":\"%s\",\"index\":\"%s\"", argv[1], argv[2]);
        }
    }
    /* swap-pane <id1> <id2> */
    else if (strcmp(cmd, "swap-pane") == 0 || strcmp(cmd, "swap_pane") == 0) {
        if (argc >= 3) {
            args_len = snprintf(args_buf, sizeof args_buf,
                                "\"id1\":\"%s\",\"id2\":\"%s\"", argv[1], argv[2]);
        }
    }
    /* break-pane [id] */
    else if (strcmp(cmd, "break-pane") == 0 || strcmp(cmd, "break_pane") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"id\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        }
    }
    /* join-pane <source_id> <destination_id> */
    else if (strcmp(cmd, "join-pane") == 0 || strcmp(cmd, "join_pane") == 0) {
        if (argc >= 3) {
            args_len = snprintf(args_buf, sizeof args_buf,
                                "\"source_id\":\"%s\",\"destination_id\":\"%s\"", argv[1], argv[2]);
        }
    }
    /* ssh <user@host> [port] */
    else if (strcmp(cmd, "ssh") == 0 && argc >= 2) {
        args_len = snprintf(args_buf, sizeof args_buf, "\"host\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        if (argc >= 3) {
            args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"port\":\"%s\"", (json_escape(argv[2], _esc, sizeof _esc), _esc));
        }
        }


    /* session.save / session.restore — no args */
    else if (strcmp(cmd, "session.save") == 0 || strcmp(cmd, "session.restore") == 0) {
        /* args: {} — args_len stays 0 */
    }
    /* config.get [key] */
    else if (strcmp(cmd, "config.get") == 0 || strcmp(cmd, "config_get") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"key\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        }
    }
    /* config.set <key> <value> */
    else if (strcmp(cmd, "config.set") == 0 || strcmp(cmd, "config_set") == 0) {
        if (argc >= 3) {
            args_len = snprintf(args_buf, sizeof args_buf,
                "\"key\":\"%s\",\"value\":\"%s\"", argv[1], argv[2]);
        }
    }
    /* workspace.group.create <name> */
    else if (strcmp(cmd, "workspace.group.create") == 0 || strcmp(cmd, "workspace_group_create") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"name\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        }
    }
    /* workspace.group.list — no args */
    else if (strcmp(cmd, "workspace.group.list") == 0 || strcmp(cmd, "workspace_group_list") == 0) {
        /* args: {} — args_len stays 0 */
    }
    /* workspace.group.add <name> <ws_id> */
    else if (strcmp(cmd, "workspace.group.add") == 0 || strcmp(cmd, "workspace_group_add") == 0) {
        if (argc >= 3) {
            args_len = snprintf(args_buf, sizeof args_buf,
                "\"name\":\"%s\",\"workspace_id\":\"%s\"", argv[1], argv[2]);
        }
    }
    /* workspace.group.remove <name> <ws_id> */
    else if (strcmp(cmd, "workspace.group.remove") == 0 || strcmp(cmd, "workspace_group_remove") == 0) {
        if (argc >= 3) {
            args_len = snprintf(args_buf, sizeof args_buf,
                "\"name\":\"%s\",\"workspace_id\":\"%s\"", argv[1], argv[2]);
        }
    }
    /* agent.spawn <name> <command> [workspace_id] */
    else if (strcmp(cmd, "agent.spawn") == 0 || strcmp(cmd, "agent_spawn") == 0) {
        if (argc >= 3) {
            args_len = snprintf(args_buf, sizeof args_buf,
                "\"name\":\"%s\",\"command\":\"%s\"", argv[1], argv[2]);
            if (argc >= 4) {
                args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"workspace_id\":\"%s\"", (json_escape(argv[3], _esc, sizeof _esc), _esc));
            }
        }
    }
    /* agent.list — no args */
    else if (strcmp(cmd, "agent.list") == 0 || strcmp(cmd, "agent_list") == 0) {
        /* args: {} — args_len stays 0 */
    }
    /* agent.stop <id> */
    else if (strcmp(cmd, "agent.stop") == 0 || strcmp(cmd, "agent_stop") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"id\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        }
    }
    /* ssh.list — list active SSH sessions */
    else if (strcmp(cmd, "ssh.list") == 0 || strcmp(cmd, "ssh_list") == 0) {
        /* args: {} — args_len stays 0 */
    }
    /* ssh.connect <host> [port] [user] */
    else if (strcmp(cmd, "ssh.connect") == 0 || strcmp(cmd, "ssh_connect") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"host\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
            if (argc >= 3) {
                args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"port\":\"%s\"", (json_escape(argv[2], _esc, sizeof _esc), _esc));
            }
            if (argc >= 4) {
                args_len += snprintf(args_buf + args_len, sizeof args_buf - args_len, ",\"user\":\"%s\"", (json_escape(argv[3], _esc, sizeof _esc), _esc));
            }
        }
    }
    /* ssh.disconnect <session_id> */
    else if (strcmp(cmd, "ssh.disconnect") == 0 || strcmp(cmd, "ssh_disconnect") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"session_id\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        }
    }
    /* hooks.list — list registered hooks */
    else if (strcmp(cmd, "hooks.list") == 0 || strcmp(cmd, "hooks_list") == 0) {
        /* args: {} — args_len stays 0 */
    }
    /* hooks.add <event> <script> */
    else if (strcmp(cmd, "hooks.add") == 0 || strcmp(cmd, "hooks_add") == 0) {
        if (argc >= 3) {
            args_len = snprintf(args_buf, sizeof args_buf,
                "\"event\":\"%s\",\"script\":\"%s\"", argv[1], (json_escape(argv[2], _esc, sizeof _esc), _esc));
        }
    }
    /* hooks.remove <event> <script> */
    else if (strcmp(cmd, "hooks.remove") == 0 || strcmp(cmd, "hooks_remove") == 0) {
        if (argc >= 3) {
            args_len = snprintf(args_buf, sizeof args_buf,
                "\"event\":\"%s\",\"script\":\"%s\"", argv[1], (json_escape(argv[2], _esc, sizeof _esc), _esc));
        }
    }
    /* naming.suggest <dir> */
    else if (strcmp(cmd, "naming.suggest") == 0 || strcmp(cmd, "naming_suggest") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"cwd\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        }
    }
    /* focus.history — show focus history */
    else if (strcmp(cmd, "focus.history") == 0 || strcmp(cmd, "focus_history") == 0) {
        if (argc >= 2) {
            args_len = snprintf(args_buf, sizeof args_buf, "\"count\":\"%s\"", (json_escape(argv[1], _esc, sizeof _esc), _esc));
        }
    }

    char *json = malloc(8192);
    if (args_len > 0) {
        snprintf(json, 8192, "{\"cmd\":\"%s\",\"args\":{%s}}", cmd, args_buf);
    } else if (strcmp(cmd, "workspace.list") == 0 ||
               strcmp(cmd, "surface.list") == 0 ||
               strcmp(cmd, "pane.list") == 0 ||
               strcmp(cmd, "notification.list") == 0 ||
               strcmp(cmd, "workspace.current") == 0 ||
               strcmp(cmd, "ping") == 0 ||
               strcmp(cmd, "tree") == 0 ||
               strcmp(cmd, "last_pane") == 0 ||
               strcmp(cmd, "last_window") == 0 ||
               strcmp(cmd, "next_window") == 0 ||
               strcmp(cmd, "previous_window") == 0 ||
               strcmp(cmd, "events") == 0 ||
               strcmp(cmd, "capabilities") == 0 ||
               strcmp(cmd, "session.save") == 0 ||
               strcmp(cmd, "session.restore") == 0 ||
               strcmp(cmd, "agent.list") == 0 ||
               strcmp(cmd, "workspace.group.list") == 0 ||
               strcmp(cmd, "ssh.list") == 0 || strcmp(cmd, "ssh_list") == 0 ||
               strcmp(cmd, "hooks.list") == 0 || strcmp(cmd, "hooks_list") == 0 ||
               strcmp(cmd, "focus.history") == 0 || strcmp(cmd, "focus_history") == 0) {
        snprintf(json, 8192, "{\"cmd\":\"%s\",\"args\":{}}", cmd);
    } else if (strcmp(cmd, "help") == 0) {
        snprintf(json, 8192, "{\"cmd\":\"help\",\"args\":{}}");
    } else if (strcmp(cmd, "version") == 0) {
        free(json);
        return NULL; /* handled locally */
    } else {
        /* Unknown command — pass through as-is */
        snprintf(json, 8192, "{\"cmd\":\"%s\",\"args\":{}}", cmd);
    }

    return json;
}

/* ------------------------------------------------------------------ */
/* Pretty-print JSON response                                           */
/* ------------------------------------------------------------------ */

static void print_response(const char *response, bool raw_json) {
    if (!response) return;

    if (raw_json) {
        printf("%s\n", response);
        return;
    }

    /* Check for error. */
    if (strstr(response, "\"ok\":false")) {
        const char *msg = strstr(response, "\"message\":\"");
        if (msg) {
            msg += 11;
            while (*msg && *msg != '"') putchar(*msg++);
            putchar('\n');
        } else {
            printf("error: %s\n", response);
        }
        return;
    }

    /* Find "result": and extract its value */
    const char *res = strstr(response, "\"result\":");
    if (!res) { puts(response); return; }
    res += 9;
    while (*res == ' ' || *res == '\t' || *res == '\n' || *res == '\r') res++;

    /* Extract value respecting nesting depth */
    char val_buf[8192];
    size_t vn = 0;
    if (*res == '{' || *res == '[') {
        char close_br = (*res == '{') ? '}' : ']';
        int depth = 0;
        val_buf[vn++] = *res;
        res++;
        while (*res && vn < sizeof val_buf - 1) {
            if (*res == '{' || *res == '[') depth++;
            if (*res == '}' || *res == ']') { if (depth == 0) break; depth--; }
            val_buf[vn++] = *res;
            res++;
        }
        val_buf[vn++] = (close_br); /* closing bracket */
        val_buf[vn] = 0;
    } else {
        while (*res && *res != ',' && *res != '}' && *res != ']' && vn < sizeof val_buf - 1) {
            val_buf[vn++] = *res++;
        }
        val_buf[vn] = 0;
    }
    res = val_buf;
    /* Object or array result. */
    /* For simple results with just id/title/version, inline them. */
    /* For single-object results (workspace.current, surface.create, surface.split etc),
     * extract id and title/kind. */
    const char *id_v = NULL, *title_v = NULL, *kind_v = NULL;
    /* Find first object */
    const char *obj = res;
    while (*obj && *obj != '{') obj++;
    if (*obj == '{') {
        id_v = strstr(obj, "\"id\":");
        title_v = strstr(obj, "\"title\":");
        kind_v = strstr(obj, "\"kind\":");
        const char *end = strchr(obj, '}');
        if (id_v && end && id_v < end) {
            id_v += 5;
            while (*id_v == ' ') id_v++;
            printf("  ");
            if (title_v && title_v < end) {
                title_v += 9;
                while (*title_v && *title_v != '"') putchar(*title_v++);
            } else if (kind_v && kind_v < end) {
                kind_v += 7;
                while (*kind_v && *kind_v != '"') putchar(*kind_v++);
            } else {
                printf("object");
            }
            printf(" (id=");
            while (*id_v && *id_v != ',' && *id_v != '}' && *id_v != ' ') putchar(*id_v++);
            printf(")\n");
            return;
        }

    /* Array results — find the array and iterate. */
    const char *arr = NULL;
    if ((arr = strstr(res, "\"workspaces\"")) ||
        (arr = strstr(res, "\"surfaces\"")) ||
        (arr = strstr(res, "\"panes\"")) ||
        (arr = strstr(res, "\"notifications\""))) {
        arr = strchr(arr, '[');
        if (arr) {
            arr++;
            while (*arr && *arr != ']') {
                /* Skip to next object */
                while (*arr && *arr != '{' && *arr != ']') arr++;
                if (*arr != '{') break;
                /* Parse one object */
                const char *oend = arr + 1;
                int depth = 1;
                while (*oend && depth > 0) {
                    if (*oend == '{') depth++;
                    if (*oend == '}') depth--;
                    oend++;
                }
                /* Save end */
                const char *obj_end = oend;
                /* Extract fields */
                const char *t = strstr(arr, "\"title\":\"");
                const char *k = strstr(arr, "\"kind\":\"");
                const char *i = strstr(arr, "\"id\":");
                const char *f = strstr(arr, "\"focused\":true");
                const char *w = strstr(arr, "\"waiting\":true");
                const char *txt = strstr(arr, "\"text\":\"");
                printf("  ");
                if (k && k < obj_end) {
                    k += 7;
                    while (*k && *k != '"') putchar(*k++);
                    printf(" ");
                }
                if (t && t < obj_end) {
                    t += 9;
                    while (*t && *t != '"') putchar(*t++);
                }
                if (txt && txt < obj_end) {
                    txt += 7;
                    while (*txt && *txt != '"') putchar(*txt++);
                }
                if (i && i < obj_end) {
                    i += 5;
                    while (*i == ' ') i++;
                    printf(" (id=");
                    while (*i && *i != ',' && *i != '}' && *i != ' ') putchar(*i++);
                    printf(")");
                }
                if (f && f < obj_end) printf(" *focused*");
                if (w && w < obj_end) printf(" [waiting]");
                printf("\n");
                arr = obj_end;
            }
            if (*arr == ']' && arr == strchr(arr, ']')) {
                /* Check if we printed nothing (empty array) */
            }
            return;
        }
    }

    } /* closes if (*obj == '{') */
    /* Fallback: print raw result */
    printf("%s\n", res);
}

/* ------------------------------------------------------------------ */
/* Main                                                                 */
/* ------------------------------------------------------------------ */

static void print_usage(void) {
    printf("lmux v%s — Linux terminal for AI coding agents\n\n", LMUX_VERSION);
    printf("Usage:\n");
    printf("  lmux <command> [args...]          Run a command (connects to lmux socket)\n");
    printf("  lmux --socket <path> <cmd> [args] Use specific socket path\n");
    printf("  lmux --json <cmd> [args]          Raw JSON output\n");
    printf("  lmux daemon                       Start headless lmux daemon\n");
    printf("  lmux help                         Show this help\n");
    printf("  lmux version                      Show version\n\n");
    printf("Commands:\n");
    printf("  workspace.create <title>          Create a workspace\n");
    printf("  workspace.list                    List workspaces\n");
    printf("  workspace.close <id>              Close a workspace\n");
    printf("  workspace.select <id>             Focus a workspace\n");
    printf("  workspace.current                 Show focused workspace\n");
    printf("  ssh <user@host> [port]             Create an SSH-backed workspace\n");
    printf("  workspace.rename <id> <title>     Rename a workspace\n");
    printf("  surface.create [ws_id] [title]    Create a surface\n");
    printf("  surface.list                      List surfaces\n");
    printf("  surface.close <id> [ws_id]        Close a surface\n");
    printf("  notification.clear                       Clear all notifications\n");
    printf("  notification.mark-read [seq]             Mark notification as read\n");
    printf("  surface.focus <id> [ws_id]               Focus a surface\n");
    printf("  surface.send-key <key> [s_id] [ws]       Send a single key to a surface\n");
    printf("  workspace.reorder <from> <to>            Reorder workspace\n");
    printf("  snapshot.save [path]               Save workspace state to file\n");
    printf("  snapshot.load [path]               Load workspace state from file\n");
    printf("  surface.split <dir> [s_id] [ws]   Split pane (dir: h|v)\n");
    printf("  pane.list                         List panes\n");
    printf("  pane.focus <id> [ws_id]           Focus a pane\n");
    printf("  surface.send_text <text>          Send text to surface\n");
    printf("  notification.create <text>        Send notification\n");
    printf("  notification.list                 List notifications\n");
    printf("  ping                              Check connectivity\n");
    printf("  display-message <message>          Echo a message back from the daemon\n");
    printf("  tree                              Print workspace/surface/pane hierarchy\n");
    printf("  pane.resize <dir> [cols] [rows] [s_id] [ws]  Resize a pane\n");
    printf("  pane.focus-dir <dx> [dy]           Move focus in a direction\n");
    printf("  last-pane                         Focus the last active pane\n");
  printf("  workspace.refresh [ws_id]           Refresh workspace metadata\n");
  printf("  read-screen [s_id] [ws]            Read terminal screen content\n");
  printf("  capture-pane [s_id] [ws]           Alias for read-screen\n");
  printf("  session.save                       Save session to disk\n");
  printf("  session.restore                    Restore session from disk\n");
  printf("  config.get [key]                   Get config value(s)\n");
  printf("  config.set <key> <value>           Set config value\n");
  printf("  workspace.group.create <name>       Create a workspace group\n");
  printf("  workspace.group.list               List workspace groups\n");
  printf("  workspace.group.add <name> <ws_id> Add workspace to group\n");
  printf("  workspace.group.remove <name> <ws_id> Remove workspace from group\n");
  printf("  agent.spawn <name> <command> [ws_id] Spawn an agent process\n");
  printf("  agent.list                         List running agents\n");
    printf("  agent.stop <id>                    Stop an agent process\n");
    printf("  ssh.list                           List active SSH sessions\n");
    printf("  ssh.connect <host> [port] [user]   Connect to remote host via SSH\n");
    printf("  ssh.disconnect <session_id>        Disconnect an SSH session\n");
    printf("  hooks.list                         List registered hook scripts\n");
    printf("  hooks.add <event> <script>         Register a hook script for an event\n");
    printf("  hooks.remove <event> <script>      Unregister a hook script\n");
    printf("  naming.suggest <dir>               Get workspace name suggestions for a directory\n");
    printf("  focus.history [count]              Show recent focus history\n");
    printf("  capabilities                       Print server capabilities\n");
}

static void print_version(void) {
    printf("lmux version %s\n", LMUX_VERSION);
}

int main(int argc, char **argv) {
    /* Default socket path. */
    const char *socket_path = NULL;
    bool raw_json = false;

    /* Parse global flags. */
    int i = 1;
    while (i < argc && argv[i][0] == '-') {
        if (strcmp(argv[i], "--socket") == 0 && i + 1 < argc) {
            socket_path = argv[++i];
        } else if (strcmp(argv[i], "--json") == 0) {
            raw_json = true;
        } else if (strcmp(argv[i], "--help") == 0 || strcmp(argv[i], "-h") == 0) {
            print_usage();
            return 0;
        } else if (strcmp(argv[i], "--version") == 0 || strcmp(argv[i], "-v") == 0) {
            print_version();
            return 0;
        } else {
            fprintf(stderr, "lmux: unknown flag: %s\n", argv[i]);
            fprintf(stderr, "Try 'lmux help' for usage.\n");
            return 1;
        }
        i++;
    }

    /* Resolve socket path from env if not given via flag. */
    if (!socket_path) {
        socket_path = getenv("LMUX_SOCKET_PATH");
    }
    if (!socket_path) {
        const char *run = getenv("XDG_RUNTIME_DIR");
        if (!run) run = "/tmp";
        static char default_path[256];
        snprintf(default_path, sizeof default_path, "%s/lmux.sock", run);
        socket_path = default_path;
    }

    /* Remaining args = command. */
    argc -= i;
    argv += i;

    if (argc < 1) {
        /* Check if lmux daemon is running by trying ping. */
        int fd = connect_socket(socket_path);
        if (fd >= 0) {
            close(fd);
            print_usage();
            return 0;
        }
        /* Print help with daemon hint if no socket. */
        print_usage();
        printf("\n(lmux daemon not running at %s)\n", socket_path);
        return 0;
    }

    const char *command = argv[0];

    /* Handle local commands. */
    if (strcmp(command, "help") == 0) {
        print_usage();
        return 0;
    }
    if (strcmp(command, "version") == 0) {
        print_version();
        return 0;
    }

    /* Daemon mode: start headless lmux with socket server. */
    if (strcmp(command, "daemon") == 0) {
        printf("Starting lmux daemon on %s...\n", socket_path);
        fflush(stdout);
        /* Detach from terminal session so SIGTERM from controlling terminal
           doesn't affect the daemon. */
        if (setsid() < 0) {
            fprintf(stderr, "lmux: setsid() failed\n");
        }
        /* Ignore SIGPIPE so write() to disconnected client sockets
           returns EPIPE instead of killing the process. */
        signal(SIGPIPE, SIG_IGN);
        lmux_app *app = lmux_app_new(socket_path);
        if (!app) {
            fprintf(stderr, "lmux: failed to create app\n");
            return 1;
        }
        /* Set up signal handlers for graceful shutdown */
        struct sigaction sa;
        memset(&sa, 0, sizeof sa);
        sa.sa_handler = handle_signal;
        sigaction(SIGINT, &sa, NULL);
        sigaction(SIGTERM, &sa, NULL);
        /* SIGCHLD: reap terminated child processes immediately (no zombies). */
        sa.sa_handler = sigchld_handler;
        sigaction(SIGCHLD, &sa, NULL);
        daemon_app_global = app;
        lmux_app_start(app);
        if (!app) {
            fprintf(stderr, "lmux: failed to create app\n");
            return 1;
        }
        /* Create a default workspace. */
        lmux_workspace_create(app, "default");
        /* Start socket server. */
        if (lmux_server_start_threaded(app) != 0) {
            fprintf(stderr, "lmux: failed to start socket server\n");
            lmux_app_free(app);
            return 1;
        }
        printf("lmux daemon ready. PID=%d\n", getpid());
        fflush(stdout);
        /* Main loop: check both app state and async exit flag. */
        while (lmux_app_is_running(app) && !daemon_should_exit) {
            lmux_app_tick(app);
            struct timespec ts = {0, 16000000L}; /* 16ms = ~60Hz */
            nanosleep(&ts, NULL);
        }
        /* Graceful shutdown: save snapshot, stop server, cleanup. */
        if (daemon_app_global) {
            char snap_path[512];
            const char *xdg = getenv("XDG_DATA_HOME");
            if (xdg) {
                snprintf(snap_path, sizeof snap_path, "%s/lmux/snapshot.json", xdg);
            } else {
                const char *home = getenv("HOME");
                if (home) {
                    snprintf(snap_path, sizeof snap_path, "%s/.local/share/lmux/snapshot.json", home);
                } else {
                    snprintf(snap_path, sizeof snap_path, "/tmp/lmux/snapshot.json");
                }
            }
            lmux_snapshot_save(daemon_app_global, snap_path);
            lmux_app_quit(daemon_app_global, 0);
        }
        lmux_server_stop(app);
        lmux_app_free(app);
        return 0;
    }
    /* Web dashboard mode: start Python HTTP server. */
    if (strcmp(command, "web") == 0) {
        const char *script = NULL;
        char path_buf[1024];
        ssize_t len = readlink("/proc/self/exe", path_buf, sizeof(path_buf) - 1);
        if (len > 0) {
            path_buf[len] = '\0';
            char *slash = strrchr(path_buf, '/');
            if (slash) {
                *slash = '\0';
                static char script_path[2048];
                int written = snprintf(script_path, sizeof(script_path), "%s/../share/lmux/web/server.py", path_buf);
                if (written > 0 && (size_t)written < sizeof(script_path)) {
                    if (access(script_path, F_OK) == 0) script = script_path;
                }
            }
        }
        if (!script) {
            const char *candidates[] = {"web/server.py", "../web/server.py", "/usr/share/lmux/web/server.py", NULL};
            for (int i = 0; candidates[i]; i++) {
                if (access(candidates[i], F_OK) == 0) { script = candidates[i]; break; }
            }
        }
        if (!script) { fprintf(stderr, "lmux: cannot find web/server.py\n"); return 1; }
        int port = 8080;
        for (int i = 2; i < argc; i++) {
            if (strcmp(argv[i], "--port") == 0 && i + 1 < argc) { port = atoi(argv[i + 1]); break; }
        }
        char port_str[16];
        snprintf(port_str, sizeof(port_str), "%d", port);
        printf("Starting lmux web dashboard on http://127.0.0.1:%d\n", port);
        fflush(stdout);
        execlp("python3", "python3", script, "--port", port_str, (char *)NULL);
        perror("lmux: failed to start web server");
        return 1;
    }

    /* Socket commands: connect, send, receive, print. */
    char *json_request = build_json_command(argc, argv);
    if (!json_request) {
        fprintf(stderr, "lmux: unknown command or missing args: %s\n", command);
        return 1;
    }

    int fd = connect_socket(socket_path);
    if (fd < 0) {
        fprintf(stderr, "lmux: is the daemon running? (socket: %s)\n", socket_path);
        fprintf(stderr, "Try 'lmux daemon' to start it.\n");
        free(json_request);
        return 1;
    }

    char *response = send_request(fd, json_request);
    close(fd);

    if (!response) {
        free(json_request);
        return 1;
    }

    print_response(response, raw_json);
    free(response);
    free(json_request);
    return 0;
}
