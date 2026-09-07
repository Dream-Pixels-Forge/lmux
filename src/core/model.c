/*
 * model.c - Workspace / surface / pane model + sidebar metadata.
 *
 * Pure C, no GLib, no GTK. Each lmux_app owns a vector of workspaces.
 * Each workspace owns a vector of surfaces. Each surface owns a
 * vector of panes. A pane is either "terminal" (with a pty-backed
 * command) or "browser" (with a URL). Split geometry is computed
 * from the parent/sibling links at render time by the host.
 *
 * Sidebar metadata (git branch, listening ports, latest notification,
 * PR label, unread/waiting flags) is refreshed on demand by
 * lmux_workspace_refresh. The refresh is intentionally simple: it
 * shells out to `git -C <cwd> rev-parse --abbrev-ref HEAD` and parses
 * /proc/net/tcp to detect listening ports for processes whose cwd
 * is a descendant of <cwd>. PR detection is by reading
 * .git/config for `gh-merge-base` and querying `gh pr view`.
 */
#define _GNU_SOURCE
#define _POSIX_C_SOURCE 200809L

#include "lmux.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <time.h>

#include <unistd.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <dirent.h>
#include <stdlib.h>
#include <signal.h>
#include <pty.h>
#include <utmp.h>
#include <pthread.h>

#include <fcntl.h>
#include <errno.h>
#include <sys/wait.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <time.h>

/* Suppress format-truncation and stringop-truncation warnings.
 * Buffer sizes are appropriate; GCC can't prove it at compile time. */
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wformat-truncation"
#pragma GCC diagnostic ignored "-Wstringop-truncation"

/* ----- Path safety validation ----- */

/**
 * Check if a path is safe (no traversal attacks).
 * Rejects paths containing ".." components, absolute paths when a base
 * directory is specified, and null bytes.
 *
 * @param path       The path to validate.
 * @param base_dir   Optional base directory (if NULL, only checks for ".." and null bytes).
 * @return true if the path is safe, false if it contains traversal sequences.
 */
static bool is_safe_path(const char *path, const char *base_dir) {
    if (!path || !path[0]) return false;

    /* Reject null bytes */
    if (strchr(path, '\0') && strchr(path, '\0') != path + strlen(path)) return false;

    /* Reject ".." components */
    const char *p = path;
    while (*p) {
        if (p[0] == '.' && p[1] == '.') {
            /* Check for ".." as a complete component */
            if ((p == path || *(p - 1) == '/') &&
                (p[2] == '/' || p[2] == '\0')) {
                return false;
            }
        }
        /* Advance to next component */
        while (*p && *p != '/') p++;
        if (*p == '/') p++;
    }

    /* If base_dir specified, reject absolute paths and verify containment */
    if (base_dir && base_dir[0]) {
        if (path[0] == '/') return false;

        /* Build resolved path: base_dir + "/" + path */
        char resolved[2048];
        snprintf(resolved, sizeof resolved, "%s/%s", base_dir, path);

        /* Normalize: resolve . and .. without following symlinks (best-effort) */
        char norm[2048];
        size_t ni = 0;
        const char *rp = resolved;
        while (*rp && ni < sizeof norm - 1) {
            if (rp[0] == '/' && rp[1] == '/') { rp++; continue; }
            if (rp[0] == '.' && (rp[1] == '/' || rp[1] == '\0')) { rp += (rp[1] == '/') ? 2 : 1; continue; }
            norm[ni++] = *rp++;
        }
        norm[ni] = 0;

        /* Check that normalized path starts with base_dir */
        size_t blen = strlen(base_dir);
        if (blen > 0 && strncmp(norm, base_dir, blen) != 0) return false;
        if (blen > 0 && norm[blen] != '/' && norm[blen] != '\0') return false;
    }

    return true;
}

/* ----- Vector helper ----- */

#define VEC_GROW 8
typedef struct {
    void **items;
    size_t len;
    size_t cap;
} vec_t;

static bool vec_push(vec_t *v, void *item) {
    if (v->len == v->cap) {
        v->cap = v->cap ? v->cap * 2 : VEC_GROW;
        void *new_items = realloc(v->items, v->cap * sizeof(void *));
        if (!new_items) return false;
        v->items = new_items;
    }
    v->items[v->len++] = item;
    return true;
}

static void vec_remove(vec_t *v, size_t idx) {
    if (idx >= v->len) return;
    memmove(&v->items[idx], &v->items[idx + 1],
            (v->len - idx - 1) * sizeof(void *));
    v->len--;
}

/* ----- Clipboard (static, shared across panes) ----- */

static char lmux_clipboard_buf[65536];
static size_t lmux_clipboard_len = 0;

static void lmux_clipboard_set(const char *text, size_t len) {
    if (len > sizeof(lmux_clipboard_buf) - 1)
        len = sizeof(lmux_clipboard_buf) - 1;
    memcpy(lmux_clipboard_buf, text, len);
    lmux_clipboard_buf[len] = '\0';
    lmux_clipboard_len = len;
}

static size_t lmux_clipboard_get(char *out, size_t cap) {
    size_t n = lmux_clipboard_len < cap ? lmux_clipboard_len : cap;
    if (n > 0) memcpy(out, lmux_clipboard_buf, n);
    return n;
}

/* ----- File Explorer (static, internal to model.c) ----- */

typedef struct {
    char    name[256];          /* file/dir base name */
    char    path[1024];         /* full path */
    bool    is_dir;             /* true if directory */
    int64_t size;               /* file size in bytes */
    time_t  mtime;              /* modification time */
} lmux_file_entry;

typedef struct {
    bool     active;            /* true if explorer is open */
    char     current_path[1024];/* current directory path */
    lmux_file_entry entries[1024];
    int      entry_count;
    int      selected_index;
    char     filter[256];       /* case-insensitive substring filter */
    int      sort_mode;         /* 0=name, 1=size, 2=time */
} lmux_file_explorer;

/* File explorer sort modes */
#define FILE_SORT_NAME  0
#define FILE_SORT_SIZE  1
#define FILE_SORT_TIME  2

#define MAX_FILE_ENTRIES 1024

/* Scan a directory and populate the file explorer entries.
 * Returns the number of entries found. */
static int file_explorer_scan_dir(lmux_file_explorer *fe, const char *path) {
    DIR *dir = opendir(path);
    if (!dir) return 0;

    fe->entry_count = 0;
    snprintf(fe->current_path, sizeof fe->current_path, "%s", path);

    struct dirent *ent;
    while ((ent = readdir(dir)) != NULL && fe->entry_count < MAX_FILE_ENTRIES) {
        /* Skip . and .. */
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0)
            continue;

        lmux_file_entry *e = &fe->entries[fe->entry_count];
        snprintf(e->name, sizeof e->name, "%s", ent->d_name);

        /* Build full path */
        if (path[strlen(path) - 1] == '/')
            snprintf(e->path, sizeof e->path, "%s%s", path, ent->d_name);
        else
            snprintf(e->path, sizeof e->path, "%s/%s", path, ent->d_name);

        /* Stat for metadata */
        struct stat st;
        if (stat(e->path, &st) == 0) {
            e->is_dir = S_ISDIR(st.st_mode);
            e->size = (int64_t)st.st_size;
            e->mtime = st.st_mtime;
        } else {
            e->is_dir = false;
            e->size = 0;
            e->mtime = 0;
        }

        fe->entry_count++;
    }
    closedir(dir);
    return fe->entry_count;
}

/* Compare functions for qsort */
static int file_compare_name(const void *a, const void *b) {
    const lmux_file_entry *ea = a, *eb = b;
    /* Directories first */
    if (ea->is_dir != eb->is_dir) return ea->is_dir ? -1 : 1;
    return strcasecmp(ea->name, eb->name);
}

static int file_compare_size(const void *a, const void *b) {
    const lmux_file_entry *ea = a, *eb = b;
    if (ea->is_dir != eb->is_dir) return ea->is_dir ? -1 : 1;
    if (ea->size < eb->size) return 1;
    if (ea->size > eb->size) return -1;
    return strcasecmp(ea->name, eb->name);
}

static int file_compare_time(const void *a, const void *b) {
    const lmux_file_entry *ea = a, *eb = b;
    if (ea->is_dir != eb->is_dir) return ea->is_dir ? -1 : 1;
    if (ea->mtime < eb->mtime) return 1;
    if (ea->mtime > eb->mtime) return -1;
    return strcasecmp(ea->name, eb->name);
}

/* Apply sort to file explorer entries */
static void file_explorer_apply_sort(lmux_file_explorer *fe) {
    if (fe->entry_count <= 1) return;
    switch (fe->sort_mode) {
        case FILE_SORT_SIZE:
            qsort(fe->entries, fe->entry_count, sizeof(lmux_file_entry), file_compare_size);
            break;
        case FILE_SORT_TIME:
            qsort(fe->entries, fe->entry_count, sizeof(lmux_file_entry), file_compare_time);
            break;
        case FILE_SORT_NAME:
        default:
            qsort(fe->entries, fe->entry_count, sizeof(lmux_file_entry), file_compare_name);
            break;
    }
}

/* Get count of visible entries (after filter) */
static int file_explorer_entry_count(const lmux_file_explorer *fe) {
    if (!fe->filter[0]) return fe->entry_count;
    int count = 0;
    for (int i = 0; i < fe->entry_count; i++) {
        if (strcasestr(fe->entries[i].name, fe->filter))
            count++;
    }
    return count;
}

/* ----- Pane ----- */

struct lmux_pane {
    lmux_id       id;
    lmux_workspace *owner;
    lmux_surface  *surface;
    char          kind[16];
    char          command[512];
    char          url[1024];
    bool          focused;
    int           pty_fd;       /* pty master fd, -1 if not spawned */
    pid_t         child_pid;    /* child process pid, 0 if not spawned */
    /* Copy mode (Vi-style text selection) */
    bool          copy_mode_active;
    int           copy_cursor_row;
    int           copy_cursor_col;
    int           copy_sel_start_row;
    int           copy_sel_start_col;
    bool          copy_selecting;
};
/* pane_new defined after lmux_workspace struct — needs ws->app dereference */
/* Spawn a child process with a pty for this pane.
 * Returns 0 on success, -1 on failure. */
static int pane_spawn_pty(lmux_pane *p) {
    if (!p || p->pty_fd >= 0) return -1;
    if (!p->command[0]) return -1;

    int master;
    pid_t pid = forkpty(&master, NULL, NULL, NULL);
    if (pid < 0) {
        lmux_log(LMUX_LOG_ERROR, "pty: forkpty failed for pane %u", p->id);
        return -1;
    }

    if (pid == 0) {
        /* Child: exec the command via shell */
        const char *shell = getenv("SHELL");
        if (!shell) shell = "/bin/sh";
        execlp(shell, shell, "-c", p->command, (char *)NULL);
        /* If exec fails, exit */
        _exit(127);
    }

    /* Parent: store fd and pid */
    p->pty_fd = master;
    p->child_pid = pid;
    lmux_log(LMUX_LOG_DEBUG, "pty: spawned pane %u, pid=%d, fd=%d",
             p->id, (int)pid, master);
    return 0;
}

/* Kill the child process and close pty for a pane. */
static void pane_kill_pty(lmux_pane *p) {
    if (!p) return;
    if (p->child_pid > 0) {
        kill(p->child_pid, SIGTERM);
        /* Give it a moment, then SIGKILL */
        int status;
        if (waitpid(p->child_pid, &status, WNOHANG) == 0) {
            struct timespec ts = {0, 50000000L}; /* 50ms */
            nanosleep(&ts, NULL);
            kill(p->child_pid, SIGKILL);
            waitpid(p->child_pid, &status, 0);
        }
        p->child_pid = 0;
    }
    if (p->pty_fd >= 0) {
        close(p->pty_fd);
        p->pty_fd = -1;
    }
}

/* ----- Surface (horizontal tab) ----- */

struct lmux_surface {
    lmux_id        id;
    lmux_workspace *owner;
    char           title[128];
    bool           focused;
    vec_t          panes;
    /* Canvas layout state */
    bool           canvas_mode;
    int            canvas_pane_x[64];
    int            canvas_pane_y[64];
    int            canvas_pane_w[64];
    int            canvas_pane_h[64];
    int            canvas_pane_z[64];
    int            canvas_pane_count;
};


/* ----- Workspace ----- */

struct lmux_workspace {
    lmux_app      *app;
    lmux_id        id;
    char           title[256];
    char           cwd[1024];
    char           git_branch[128];
    char           pr_label[64];
    char           ports[256];
    char           last_notify[512];
    bool           unread;
    bool           waiting;
    bool           focused;
    char           ssh_host[256];     /* SSH target: user@host[:port] or just host */
    int            ssh_port;          /* SSH port, 0 = default 22 */
    bool           ssh_forward_agent; /* -A flag */
    vec_t          surfaces;
    vec_t          env_vars;      /* array of char* in "KEY=VALUE" format */
    vec_t          auth_tokens;   /* future use */
};



/* ----- Data paths ----- */
static void lmux_data_path(char *buf, size_t cap, const char *file) {
    const char *xdg = getenv("XDG_DATA_HOME");
    if (xdg) snprintf(buf, cap, "%s/lmux/%s", xdg, file);
    else {
        const char *home = getenv("HOME");
        if (home) snprintf(buf, cap, "%s/.local/share/lmux/%s", home, file);
        else snprintf(buf, cap, "/tmp/lmux/%s", file);
    }
}
struct lmux_app {
    char        socket_path[256];
    int         listen_fd;        /* unix socket */
    int         exit_code;
    volatile sig_atomic_t running; /* signal-safe shutdown flag */
    lmux_log_level log_level;
    char        config_path[512];  /* path to loaded config */
    char        auto_save_path[512];
    int         auto_save_interval;  /* seconds, 0=disabled */
    time_t      auto_save_last;     /* timestamp of last auto-save */
    time_t      start_time;         /* when the app was created */
    bool        restore_on_start;
    vec_t       workspaces;
    vec_t       notifications;     /* of lmux_notification* */
    uint64_t    notify_seq;
    vec_t       event_queue;      /* of char* (JSON event strings) */
    pthread_rwlock_t rw_lock;     /* read-write lock: reads don't block reads */
    pthread_mutex_t event_lock;
    uint64_t    event_seq;            /* auto-incrementing */
    char        boot_id[64];          /* generated once per boot */
    vec_t       event_replay;         /* of char* (last 1024 events) */
    FILE       *event_log_fp;         /* ~/.local/share/lmux/events.jsonl */
    long        event_log_bytes;      /* current size of event log (avoids ftell) */
    char        event_log_path[512];
    lmux_pane     *last_focused;          /* for last-pane */
    lmux_workspace *ws_current;           /* currently focused workspace */
    lmux_workspace *ws_last;              /* previously focused workspace for last-window */
    vec_t       agents;               /* of lmux_agent* */
    vec_t       wait_points;          /* of lmux_wait_point* */
    lmux_id     next_agent_id;        /* auto-increment agent id */
    lmux_id     next_surface_id;      /* auto-increment surface id */
    lmux_id     next_pane_id;         /* auto-increment pane id */
    lmux_id     next_workspace_id;    /* auto-increment workspace id */
    lmux_config *config;              /* configuration object */
    /* Metrics */
    uint64_t    metrics_requests;     /* total requests served */
    uint64_t    metrics_errors;       /* total error responses */
    uint64_t    metrics_connections;  /* total connections accepted */
    /* Notification rings */
    vec_t       notification_rings;   /* of lmux_notification_ring* */
    /* Notification hooks */
    vec_t       notification_hooks;   /* of lmux_notification_hook* */
    /* Window management */
    vec_t       windows;              /* of lmux_window* */
    lmux_id     next_window_id;       /* auto-increment window id */
    /* SSH sessions */
    vec_t       ssh_sessions;         /* of lmux_ssh_session* */
    lmux_id     next_ssh_session_id;  /* auto-increment session id */
    /* Feed panels */
    vec_t       feed_panels;          /* of lmux_feed_panel* */
    vec_t       feed_entries;         /* of lmux_feed_entry* */
    lmux_id     next_feed_panel_id;   /* auto-increment panel id */
    /* File explorer (static, internal) */
    lmux_file_explorer file_explorer;
    /* Search state */
    bool        search_active;        /* true if search mode is active */
    char        search_query[256];    /* current search query */
    int         search_match_count;   /* total matches found */
    int         search_current_match; /* current match index (0-based) */
    bool        search_case_sensitive; /* case sensitive search */
    bool        search_whole_words;   /* whole words only */
    bool        search_regex;         /* use regex */
    char        search_scope[16];     /* "current" or "all" */
    lmux_pane  *search_pane;          /* pane being searched (for current scope) */
    /* Clipboard history */
    char        clipboard[100][2048]; /* clipboard text entries */
    int         clipboard_count;      /* number of entries */
    int         clipboard_head;       /* circular buffer head */
    /* Workspace templates */
    char        templates[32][64];    /* template names */
    int         template_count;       /* number of templates */
    /* Performance profiling */
    bool        profile_active;       /* true if profiling is active */
    char        profile_label[128];   /* current profiling label */
    struct timespec profile_start;    /* profiling start time */
    struct { char label[128]; long elapsed_ms; } profile_results[32]; /* last 32 results */
    int         profile_result_count; /* number of stored results */
    /* Cloud VMs */
    struct { char id[64]; char name[64]; char provider[32]; char size[32]; char status[16]; } vms[32];
    int         vm_count;
    /* iOS Companions */
    struct { char id[64]; char name[64]; char platform[16]; bool connected; time_t last_seen; } companions[16];
    int         companion_count;
    /* Agent Teams */
    struct { char id[64]; char name[64]; struct { char name[64]; char role[32]; } members[8]; int member_count; } teams[16];
    int         team_count;
};
static lmux_pane *pane_new(lmux_workspace *ws, lmux_surface *s,
                           const char *kind) {
    lmux_pane *p = calloc(1, sizeof *p);
    if (!p) return NULL;
    p->id       = ws->app->next_pane_id++;
    p->owner    = ws;
    p->surface  = s;
    snprintf(p->kind, sizeof p->kind, "%s", kind);
    p->focused  = false;
    p->pty_fd   = -1;
    p->child_pid = 0;
    return p;
}

static lmux_surface *surface_new(lmux_workspace *ws) {
    lmux_surface *s = calloc(1, sizeof *s);
    if (!s) return NULL;
    s->id     = ws->app->next_surface_id++;
    s->owner  = ws;
    s->focused = false;
    return s;
}


/* ------------------------------------------------------------------ */
/* App lifecycle                                                      */
/* ------------------------------------------------------------------ */

/* Forward declarations for static JSON helpers (used below in app lifecycle) */
static const char *json_find_key(const char *json, const char *key);
static bool json_extract_string(const char *json, const char *key, char *out, size_t cap);
static bool json_extract_bool(const char *json, const char *key, bool *out);

lmux_app *lmux_app_new(const char *socket_path) {
    lmux_app *a = calloc(1, sizeof *a);
    if (socket_path) {
        snprintf(a->socket_path, sizeof a->socket_path, "%s", socket_path);
    } else {
        const char *run = getenv("XDG_RUNTIME_DIR");
        if (!run) run = "/tmp";
        snprintf(a->socket_path, sizeof a->socket_path,
                 "%s/lmux.sock", run);
    }
    pthread_rwlock_init(&a->rw_lock, NULL);
    a->event_lock = (pthread_mutex_t)PTHREAD_MUTEX_INITIALIZER;
    a->event_queue = (vec_t){0};
    a->agents = (vec_t){0};
    a->wait_points = (vec_t){0};
    a->windows = (vec_t){0};
    a->ssh_sessions = (vec_t){0};
    a->feed_panels = (vec_t){0};
    a->feed_entries = (vec_t){0};
    a->next_agent_id = 1;
    a->next_surface_id = 1;
    a->next_pane_id = 1;
    a->next_workspace_id = 1;
    a->next_window_id = 1;
    a->next_ssh_session_id = 1;
    a->next_feed_panel_id = 1;
    a->log_level = LMUX_LOG_INFO;
    a->start_time = time(NULL);

    /* L2 fix: read config file ONCE, reuse buffer for both config and legacy overrides. */
    a->config = lmux_config_new();
    /* Config path defaults */
    lmux_config_path(a->config_path, sizeof a->config_path);
    lmux_data_path(a->auto_save_path, sizeof a->auto_save_path, "snapshot.json");

    /* Read config file once into buffer */
    char cbuf[32768];
    size_t cn = 0;
    FILE *cf = fopen(a->config_path, "r");
    if (cf) {
        cn = fread(cbuf, 1, sizeof cbuf - 1, cf);
        fclose(cf);
        cbuf[cn] = 0;
    }

    /* Parse structured config from same buffer */
    if (a->config && cn > 0) {
        lmux_config_load_buf(a->config, cbuf, cn);
        if (a->config->auto_save_session) {
            a->auto_save_interval = a->config->auto_save_interval_sec;
        } else {
            a->auto_save_interval = 0;
        }
        a->restore_on_start = a->config->auto_save_session;
    }

    if (!a->auto_save_interval) a->auto_save_interval = 0;

    /* Legacy overrides from same buffer (no second file read) */
    if (cn > 0) {
        char tmp[128] = {0};
        json_extract_string(cbuf, "log_level", tmp, sizeof tmp);
        if (strcmp(tmp, "debug") == 0) a->log_level = LMUX_LOG_DEBUG;
        else if (strcmp(tmp, "warn") == 0) a->log_level = LMUX_LOG_WARN;
        else if (strcmp(tmp, "error") == 0) a->log_level = LMUX_LOG_ERROR;
        memset(tmp, 0, sizeof tmp);
        json_extract_string(cbuf, "auto_save_path", tmp, sizeof tmp);
        if (tmp[0]) snprintf(a->auto_save_path, sizeof a->auto_save_path, "%s", tmp);
        bool auto_save = false;
        json_extract_bool(cbuf, "auto_save", &auto_save);
        if (auto_save && !a->auto_save_interval) {
            a->auto_save_interval = 30;
        }
        json_extract_bool(cbuf, "restore_on_start", &a->restore_on_start);
    }

    /* Generate boot_id from kernel UUID for high entropy */
    {
        FILE *uuid_f = fopen("/proc/sys/kernel/random/uuid", "r");
        if (uuid_f) {
            if (fgets(a->boot_id, sizeof a->boot_id, uuid_f)) {
                /* Remove trailing newline */
                size_t len = strlen(a->boot_id);
                if (len > 0 && a->boot_id[len - 1] == '\n') a->boot_id[len - 1] = 0;
            }
            fclose(uuid_f);
        } else {
            /* Fallback: seed-based */
            srand((unsigned int)(time(NULL) ^ (uintptr_t)a));
            snprintf(a->boot_id, sizeof a->boot_id, "%08x%08x%08x%08x",
                (unsigned)rand(), (unsigned)rand(), (unsigned)rand(), (unsigned)rand());
        }
    }

    /* Set up event log */
    lmux_data_path(a->event_log_path, sizeof a->event_log_path, "events.jsonl");
    char dirbuf[512];
    snprintf(dirbuf, sizeof dirbuf, "%s", a->event_log_path);
    char *slash = strrchr(dirbuf, '/');
    if (slash) { *slash = 0; mkdir(dirbuf, 0755); }
    a->event_log_fp = fopen(a->event_log_path, "a");
    a->event_log_bytes = a->event_log_fp ? ftell(a->event_log_fp) : 0;
    /* NOTE: auto-restore moved to lmux_daemon_start() in main.c
       so the socket is created and listening BEFORE restoring
       potentially large snapshots. Clients can connect immediately. */

    return a;
}

void lmux_app_free(lmux_app *a) {
    if (!a) return;
    /* Free workspaces, surfaces, panes. */
    for (size_t i = 0; i < a->workspaces.len; i++) {
        lmux_workspace *ws = a->workspaces.items[i];
        for (size_t j = 0; j < ws->surfaces.len; j++) {
            lmux_surface *s = ws->surfaces.items[j];
            for (size_t k = 0; k < s->panes.len; k++) {
                pane_kill_pty(s->panes.items[k]);
                free(s->panes.items[k]);
            }
            free(s->panes.items);
            free(s);
        }
        free(ws->surfaces.items);
        free(ws);
    }
    free(a->workspaces.items);
    for (size_t i = 0; i < a->notifications.len; i++) {
        free(a->notifications.items[i]);
    }
    for (size_t i = 0; i < a->event_queue.len; i++) {
        free(a->event_queue.items[i]);
    }
    free(a->event_queue.items);
    a->event_queue.items = NULL;
    /* Clean up event replay buffer */
    for (size_t i = 0; i < a->event_replay.len; i++) {
        free(a->event_replay.items[i]);
    }
    free(a->event_replay.items);
    /* Clean up agents */
    for (size_t i = 0; i < a->agents.len; i++) {
        lmux_agent *ag = a->agents.items[i];
        if (ag && ag->pid > 0) kill(ag->pid, SIGTERM);
        free(ag);
    }
    free(a->agents.items);
    /* Clean up wait points */
    for (size_t i = 0; i < a->wait_points.len; i++) {
        free(a->wait_points.items[i]);
    }
    free(a->wait_points.items);
    /* Close event log */
    if (a->event_log_fp) fclose(a->event_log_fp);
    free(a->notifications.items);
    /* Free configuration */
    lmux_config_free(a->config);
    pthread_rwlock_destroy(&a->rw_lock);
    pthread_mutex_destroy(&a->event_lock);
    free(a);
}

/* Global log level shared by all apps in process. */
static lmux_log_level g_log_level = LMUX_LOG_INFO;

/* JSON logging mode: enabled via LMUX_LOG_JSON=1 environment variable. */
static int g_log_json = -1;  /* -1 = not checked yet */

static int log_json_enabled(void) {
    if (g_log_json < 0) {
        const char *val = getenv("LMUX_LOG_JSON");
        g_log_json = (val && (strcmp(val, "1") == 0 || strcmp(val, "true") == 0)) ? 1 : 0;
    }
    return g_log_json;
}

/* Escape a string for safe inclusion in a JSON string value.
 * Writes to `out` (capacity `cap`), returns bytes written (excluding NUL). */
static size_t log_json_escape(const char *in, char *out, size_t cap) {
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

void lmux_log_set_level(lmux_log_level level) {
    g_log_level = level;
}

void lmux_log(lmux_log_level level, const char *fmt, ...) {
    if (level < g_log_level) return;
    static const char *names[] = {"DEBUG", "INFO ", "WARN ", "ERROR"};
    static const char *json_levels[] = {"debug", "info", "warn", "error"};

    if (log_json_enabled()) {
        /* JSON structured log: {"timestamp":"...","level":"...","message":"...","service":"lmux"} */
        time_t now = time(NULL);
        struct tm tm_buf;
        struct tm *tm = localtime_r(&now, &tm_buf);
        char timestamp[32];
        strftime(timestamp, sizeof timestamp, "%Y-%m-%dT%H:%M:%S", tm);

        /* Format the message into a buffer */
        char msg_buf[4096];
        va_list ap;
        va_start(ap, fmt);
        vsnprintf(msg_buf, sizeof msg_buf, fmt, ap);
        va_end(ap);

        /* Escape the message for JSON */
        char escaped[8192];
        log_json_escape(msg_buf, escaped, sizeof escaped);

        fprintf(stderr,
            "{\"timestamp\":\"%s\",\"level\":\"%s\",\"message\":\"%s\",\"service\":\"lmux\"}\n",
            timestamp, json_levels[level], escaped);
    } else {
        /* Legacy plain-text format */
        fprintf(stderr, "[lmux %s] ", names[level]);
        va_list ap;
        va_start(ap, fmt);
        vfprintf(stderr, fmt, ap);
        va_end(ap);
        fputc('\n', stderr);
    }
}

/**
 * Log a request/response with a correlation ID for distributed tracing.
 *
 * @param request_id  Unique ID for this request (e.g., "req-12345").
 * @param cmd         The command name (e.g., "workspace.create").
 * @param ok          Whether the request succeeded.
 * @param detail      Optional detail message (can be NULL).
 */
void lmux_log_request(const char *request_id, const char *cmd, bool ok, const char *detail) {
    if (g_log_level > LMUX_LOG_INFO) return;

    if (log_json_enabled()) {
        time_t now = time(NULL);
        struct tm tm_buf;
        struct tm *tm = localtime_r(&now, &tm_buf);
        char timestamp[32];
        strftime(timestamp, sizeof timestamp, "%Y-%m-%dT%H:%M:%S", tm);

        char cmd_escaped[256], detail_escaped[512];
        log_json_escape(cmd ? cmd : "", cmd_escaped, sizeof cmd_escaped);
        log_json_escape(detail ? detail : "", detail_escaped, sizeof detail_escaped);

        fprintf(stderr,
            "{\"timestamp\":\"%s\",\"level\":\"info\",\"service\":\"lmux\","
            "\"trace_id\":\"%s\",\"cmd\":\"%s\",\"ok\":%s",
            timestamp, request_id ? request_id : "null", cmd_escaped,
            ok ? "true" : "false");
        if (detail && detail[0]) {
            fprintf(stderr, ",\"detail\":\"%s\"", detail_escaped);
        }
        fprintf(stderr, "}\n");
    } else {
        /* Legacy format */
        fprintf(stderr, "[lmux INFO ] %s: %s %s\n",
                request_id ? request_id : "-", cmd ? cmd : "-",
                ok ? "OK" : "FAIL");
    }
}

void lmux_app_quit(lmux_app *a, int code) {
    /* Auto-save session before quitting */
    if (a && a->auto_save_path[0]) {
        lmux_snapshot_save(a, a->auto_save_path);
    }
    a->running    = false;
    a->exit_code  = code;
}

void lmux_app_tick(lmux_app *a) {
    if (!a) return;
    /* Auto-save if enabled */
    if (a->auto_save_interval > 0 && a->auto_save_path[0]) {
        time_t now = time(NULL);
        if (a->auto_save_last == 0) a->auto_save_last = now;
        else if (now - a->auto_save_last >= a->auto_save_interval) {
            lmux_snapshot_save(a, a->auto_save_path);
            a->auto_save_last = now;
        }
    }
}

bool lmux_app_is_running(const lmux_app *app) {
    return app && app->running;
}
void lmux_app_start(lmux_app *a) {
    if (a) a->running = true;
}

int lmux_app_run(lmux_app *a) {
    a->running = true;
    while (a->running) {
        lmux_app_tick(a);
        struct timespec ts = {0, 16000000L}; /* 16ms = ~60Hz */
        nanosleep(&ts, NULL);
    }
    return a->exit_code;
}

/* ------------------------------------------------------------------ */
/* Workspace operations                                              */
/* ------------------------------------------------------------------ */

lmux_workspace *lmux_workspace_create(lmux_app *a, const char *title) {
    lmux_workspace *ws = calloc(1, sizeof *ws);
    if (!ws) return NULL;
    ws->id     = a->next_workspace_id++;
    ws->app    = a;
    snprintf(ws->title, sizeof ws->title, "%s", title ? title : "Workspace");
    /* Default cwd: $PWD or $HOME. */
    const char *pwd = getenv("PWD");
    if (!pwd) pwd = getenv("HOME");
    if (pwd) snprintf(ws->cwd, sizeof ws->cwd, "%s", pwd);
    /* Create one default surface. */
    lmux_surface *s = surface_new(ws);
    if (!s) { free(ws); return NULL; }
    snprintf(s->title, sizeof s->title, "Shell");
    s->focused = true;
    vec_push(&ws->surfaces, s);
    /* Create one default pane: terminal with $SHELL or /bin/sh. */
    lmux_pane *p = pane_new(ws, s, "terminal");
    if (!p) { free(s); free(ws); return NULL; }
    const char *sh = getenv("SHELL");
    snprintf(p->command, sizeof p->command, "%s", sh ? sh : "/bin/sh");
    vec_push(&s->panes, p);
    pane_spawn_pty(p);
    ws->focused = true;
    a->ws_last = a->ws_current;
    a->ws_current = ws;
    vec_push(&a->workspaces, ws);
    return ws;
}

void lmux_workspace_close(lmux_app *a, lmux_workspace *ws) {
    for (size_t i = 0; i < a->workspaces.len; i++) {
        if (a->workspaces.items[i] == ws) {
            vec_remove(&a->workspaces, i);
            for (size_t j = 0; j < ws->surfaces.len; j++) {
                lmux_surface *s = ws->surfaces.items[j];
                for (size_t k = 0; k < s->panes.len; k++) {
                    pane_kill_pty(s->panes.items[k]);
                    free(s->panes.items[k]);
                }
                free(s->panes.items);
                free(s);
            }
            for (size_t ei = 0; ei < ws->env_vars.len; ei++) {
                free(ws->env_vars.items[ei]);
            }
            free(ws->env_vars.items);
            free(ws->auth_tokens.items);
            free(ws->surfaces.items);
            free(ws);
            return;
        }
    }
}

/* Validate SSH target: only allows alphanumeric, @, ., -, _, :, /, and whitespace.
 * Returns true if the target is safe to use in a shell command. */
static bool lmux_ssh_target_valid(const char *target) {
    if (!target || !*target) return false;
    /* Must not start with a dash (prevents option injection) */
    if (target[0] == '-') return false;
    for (const char *p = target; *p; p++) {
        unsigned char c = (unsigned char)*p;
        if (c >= 'a' && c <= 'z') continue;
        if (c >= 'A' && c <= 'Z') continue;
        if (c >= '0' && c <= '9') continue;
        if (c == '@' || c == '.' || c == '-' || c == '_' || c == ':') continue;
        if (c == ' ' || c == '\t') continue; /* allow spaces in user@host */
        /* Reject everything else (semicolons, backticks, $, etc.) */
        return false;
    }
    return true;
}

lmux_workspace *lmux_workspace_ssh_create(lmux_app *app, const char *ssh_target, int port) {
    if (!ssh_target || !*ssh_target) return NULL;

    /* Validate SSH target to prevent command injection */
    if (!lmux_ssh_target_valid(ssh_target)) {
        lmux_log(LMUX_LOG_ERROR, "ssh: invalid target '%s' (contains forbidden characters)", ssh_target);
        return NULL;
    }

    /* Create workspace with the SSH target as title */
    lmux_workspace *ws = lmux_workspace_create(app, ssh_target);
    if (!ws) return NULL;

    /* Set SSH metadata */
    snprintf(ws->ssh_host, sizeof ws->ssh_host, "%s", ssh_target);
    ws->ssh_port = port > 0 ? port : 22;

    /* Override the default pane's command to be SSH */
    if (ws->surfaces.len > 0) {
        lmux_surface *s = ws->surfaces.items[0];
        if (s->panes.len > 0) {
            lmux_pane *p = s->panes.items[0];
            /* Build SSH command with proper argument separation (not shell interpolation) */
            if (port > 0 && port != 22) {
                /* Use -p flag with numeric port only */
                snprintf(p->command, sizeof p->command, "ssh -p %d -- %s", port, ssh_target);
            } else {
                snprintf(p->command, sizeof p->command, "ssh -- %s", ssh_target);
            }
            /* Restart the pty with the SSH command */
            pane_kill_pty(p);
            pane_spawn_pty(p);
        }
    }

    return ws;
}

size_t lmux_workspace_count(lmux_app *a) { return a->workspaces.len; }

lmux_workspace *lmux_workspace_focused(lmux_app *a) {
    for (size_t i = 0; i < a->workspaces.len; i++) {
        lmux_workspace *ws = a->workspaces.items[i];
        if (ws->focused) return ws;
    }
    return a->workspaces.len ? a->workspaces.items[0] : NULL;
}

lmux_workspace *lmux_workspace_by_index(lmux_app *a, size_t i) {
    return i < a->workspaces.len ? a->workspaces.items[i] : NULL;
}

lmux_workspace *lmux_workspace_by_id(lmux_app *a, lmux_id id) {
    for (size_t i = 0; i < a->workspaces.len; i++) {
        lmux_workspace *ws = a->workspaces.items[i];
        if (ws->id == id) return ws;
    }
    return NULL;
}

bool lmux_workspace_get_info(lmux_workspace *ws, lmux_workspace_info *out) {
    if (!ws || !out) return false;
    memset(out, 0, sizeof *out);
    snprintf(out->id,    sizeof out->id,    "%u", ws->id);
    snprintf(out->title, sizeof out->title, "%s", ws->title);
    snprintf(out->cwd,   sizeof out->cwd,   "%s", ws->cwd);
    snprintf(out->git_branch, sizeof out->git_branch, "%s", ws->git_branch);
    snprintf(out->pr_label,   sizeof out->pr_label,   "%s", ws->pr_label);
    snprintf(out->ports,      sizeof out->ports,      "%s", ws->ports);
    snprintf(out->last_notify,sizeof out->last_notify,"%s", ws->last_notify);
    out->unread  = ws->unread;
    out->waiting = ws->waiting;
    return true;
}

void lmux_workspace_set_title(lmux_workspace *ws, const char *title) {
    if (title) snprintf(ws->title, sizeof ws->title, "%s", title);
}

void lmux_workspace_set_cwd(lmux_workspace *ws, const char *cwd) {
    if (cwd) snprintf(ws->cwd, sizeof ws->cwd, "%s", cwd);
    /* Invalidate cached git/ports info. */
    ws->git_branch[0] = 0;
    ws->pr_label[0]   = 0;
    ws->ports[0]      = 0;
}

/* ------------------------------------------------------------------ */
/* Surface operations                                                */
/* ------------------------------------------------------------------ */

lmux_surface *lmux_surface_create(lmux_workspace *ws, const char *title) {
    lmux_surface *s = surface_new(ws);
    snprintf(s->title, sizeof s->title, "%s", title ? title : "Tab");
    /* Mark all other surfaces in the workspace as not focused. */
    for (size_t i = 0; i < ws->surfaces.len; i++) {
        lmux_surface *o = ws->surfaces.items[i];
        o->focused = false;
    }
    s->focused = true;
    /* Create one default terminal pane. */
    lmux_pane *p = pane_new(ws, s, "terminal");
    const char *sh = getenv("SHELL");
    snprintf(p->command, sizeof p->command, "%s", sh ? sh : "/bin/sh");
    vec_push(&s->panes, p);
    pane_spawn_pty(p);
    vec_push(&ws->surfaces, s);
    return s;
}

void lmux_surface_close(lmux_workspace *ws, lmux_surface *s) {
    for (size_t i = 0; i < ws->surfaces.len; i++) {
        if (ws->surfaces.items[i] == s) {
            for (size_t k = 0; k < s->panes.len; k++) {
                pane_kill_pty(s->panes.items[k]);
                free(s->panes.items[k]);
            }
            free(s->panes.items);
            vec_remove(&ws->surfaces, i);
            if (s->focused && ws->surfaces.len) {
                lmux_surface *n = ws->surfaces.items[0];
                n->focused = true;
            }
            free(s);
            return;
        }
    }
}

size_t lmux_surface_count(lmux_workspace *ws) { return ws->surfaces.len; }

lmux_surface *lmux_surface_focused(lmux_workspace *ws) {
    for (size_t i = 0; i < ws->surfaces.len; i++) {
        lmux_surface *s = ws->surfaces.items[i];
        if (s->focused) return s;
    }
    return ws->surfaces.len ? ws->surfaces.items[0] : NULL;
}

bool lmux_surface_get_info(lmux_surface *s, lmux_surface_info *out) {
    if (!s || !out) return false;
    memset(out, 0, sizeof *out);
    out->id      = s->id;
    snprintf(out->title, sizeof out->title, "%s", s->title);
    out->focused = s->focused;
    return true;
}

void lmux_surface_set_title(lmux_surface *s, const char *title) {
    if (title) snprintf(s->title, sizeof s->title, "%s", title);
}

/* ------------------------------------------------------------------ */
/* Pane operations                                                   */
/* ------------------------------------------------------------------ */

lmux_pane *lmux_pane_split(lmux_workspace *ws, lmux_surface *s,
                           lmux_split_dir dir, const char *command,
                           bool spawn_pty) {
    (void)dir;
    if (!s) return NULL;  /* can't split without a surface */
    lmux_pane *p = pane_new(ws, s, "terminal");
    const char *sh_cmd = command ? command : (getenv("SHELL") ? getenv("SHELL") : "/bin/sh");
    snprintf(p->command, sizeof p->command, "%s", sh_cmd);
    vec_push(&s->panes, p);
    if (spawn_pty) pane_spawn_pty(p);
    return p;
}

void lmux_pane_close(lmux_workspace *ws, lmux_pane *p) {
    if (!p) return;
    for (size_t i = 0; i < ws->surfaces.len; i++) {
        lmux_surface *s = ws->surfaces.items[i];
        for (size_t j = 0; j < s->panes.len; j++) {
            if (s->panes.items[j] == p) {
                pane_kill_pty(p);
                vec_remove(&s->panes, j);
                free(p);
                return;
            }
        }
    }
}

size_t lmux_pane_count(lmux_workspace *ws) {
    size_t n = 0;
    for (size_t i = 0; i < ws->surfaces.len; i++) {
        lmux_surface *s = ws->surfaces.items[i];
        n += s->panes.len;
    }
    return n;
}

lmux_pane *lmux_pane_focused(lmux_workspace *ws) {
    for (size_t i = 0; i < ws->surfaces.len; i++) {
        lmux_surface *s = ws->surfaces.items[i];
        for (size_t j = 0; j < s->panes.len; j++) {
            lmux_pane *p = s->panes.items[j];
            if (p->focused) return p;
        }
    }
    /* Fallback: first pane of focused surface. */
    lmux_surface *sf = lmux_surface_focused(ws);
    if (sf && sf->panes.len) return sf->panes.items[0];
    return NULL;
}

void lmux_pane_focus(lmux_workspace *ws, lmux_pane *p) {
    for (size_t i = 0; i < ws->surfaces.len; i++) {
        lmux_surface *s = ws->surfaces.items[i];
        for (size_t j = 0; j < s->panes.len; j++) {
            lmux_pane *q = s->panes.items[j];
            q->focused = (q == p);
        }
    }
    ws->app->last_focused = p;
}

void lmux_pane_focus_dir(lmux_workspace *ws, int dx, int dy) {
    /* The full directional focus requires geometry; for now we cycle
     * within the focused surface's panes. The GTK host overrides this
     * with a geometry-aware implementation. */
    lmux_surface *s = lmux_surface_focused(ws);
    if (!s || s->panes.len < 2) return;
    size_t idx = 0;
    for (size_t j = 0; j < s->panes.len; j++) {
        if (((lmux_pane *)s->panes.items[j])->focused) { idx = j; break; }
    }
    if (dx > 0 || dy > 0) idx = (idx + 1) % s->panes.len;
    else                  idx = (idx + s->panes.len - 1) % s->panes.len;
    for (size_t j = 0; j < s->panes.len; j++) {
        ((lmux_pane *)s->panes.items[j])->focused = (j == idx);
    }
}

bool lmux_pane_get_info(lmux_pane *p, lmux_pane_info *out) {
    if (!p || !out) return false;
    memset(out, 0, sizeof *out);
    out->id      = p->id;
    snprintf(out->kind, sizeof out->kind, "%s", p->kind);
    out->focused = p->focused;
    return true;
}
bool lmux_pane_pty_set_size(lmux_pane *p, int cols, int rows) {
    if (!p || p->pty_fd < 0) return false;
    struct winsize ws;
    memset(&ws, 0, sizeof ws);
    ws.ws_col = (unsigned short)(cols > 0 ? cols : 80);
    ws.ws_row = (unsigned short)(rows > 0 ? rows : 24);
    return ioctl(p->pty_fd, TIOCSWINSZ, &ws) == 0;
}

void lmux_pane_send_keys(lmux_pane *p, const char *keys) {
    if (!p || !keys) return;
    if (p->pty_fd < 0) {
        /* Attempt to spawn if not yet running */
        if (pane_spawn_pty(p) != 0) {
            lmux_log(LMUX_LOG_DEBUG, "send_keys: no pty for pane %u", p->id);
            return;
        }
    }
    ssize_t n = write(p->pty_fd, keys, strlen(keys));
    if (n < 0) {
        lmux_log(LMUX_LOG_ERROR, "send_keys: write failed for pane %u: %s",
                 p->id, strerror(errno));
    } else {
        lmux_log(LMUX_LOG_DEBUG, "send_keys: wrote %zd bytes to pane %u",
                 n, p->id);
    }
}

void lmux_browser_open_url(lmux_pane *p, const char *url) {
    if (!p || !url) return;
    snprintf(p->url, sizeof p->url, "%s", url);
    /* The host actually navigates the WebKit view. */
    lmux_log(LMUX_LOG_INFO, "browser open: %s", url);
}

char *lmux_browser_eval_js(lmux_pane *p, const char *script) {
    (void)p; (void)script;
    /* The host evaluates via WebKit's run_javascript. */
    return strdup("{\"ok\":false,\"error\":{\"code\":\"no_browser\",\"message\":\"no browser backend in headless build\"}}");
}

char *lmux_browser_snapshot(lmux_pane *p) {
    (void)p;
    return strdup("{\"tree\":[]}");
}

/* ------------------------------------------------------------------ */
/* Notifications                                                       */
/* ------------------------------------------------------------------ */

void lmux_notify(lmux_app *app, const char *text, bool waiting) {
    if (!app || !text) return;
    lmux_notification *n = calloc(1, sizeof *n);
    app->notify_seq++;
    n->seq = app->notify_seq;
    snprintf(n->text, sizeof n->text, "%s", text);
    n->waiting = waiting;
    n->created_at = time(NULL);
    n->workspace_id[0] = 0;
    vec_push(&app->notifications, n);
    lmux_log(LMUX_LOG_INFO, "notify: %s (waiting=%d)", text, waiting);
}

void lmux_workspace_notify(lmux_workspace *ws, const char *text, bool waiting) {
    if (!ws || !text) return;
    lmux_app *app = ws->app;
    lmux_notification *n = calloc(1, sizeof *n);
    app->notify_seq++;
    n->seq = app->notify_seq;
    snprintf(n->text, sizeof n->text, "%s", text);
    n->waiting = waiting;
    n->created_at = time(NULL);
    snprintf(n->workspace_id, sizeof n->workspace_id, "%u", ws->id);
    vec_push(&app->notifications, n);
    /* Update workspace metadata */
    snprintf(ws->last_notify, sizeof ws->last_notify, "%s", text);
    ws->unread  = true;
    ws->waiting = waiting;
    lmux_log(LMUX_LOG_INFO, "workspace_notify[%u]: %s (waiting=%d)",
             ws->id, text, waiting);
}

size_t lmux_notification_count(lmux_app *app) {
    return app ? app->notifications.len : 0;
}

const lmux_notification *lmux_notification_at(lmux_app *app, size_t index) {
    if (!app || index >= app->notifications.len) return NULL;
    return app->notifications.items[index];
}

void lmux_notification_mark_read(lmux_app *app, uint64_t seq) {
    if (!app) return;
    for (size_t i = 0; i < app->notifications.len; i++) {
        lmux_notification *n = app->notifications.items[i];
        if (n->seq == seq) {
            n->waiting = false;
            /* Find and update the owning workspace */
            if (n->workspace_id[0]) {
                lmux_id wid = (lmux_id)atol(n->workspace_id);
                lmux_workspace *ws = lmux_workspace_by_id(app, wid);
                if (ws) {
                    ws->unread = false;
                    /* Only clear waiting if no other notification is waiting */
                    bool any_waiting = false;
                    for (size_t j = 0; j < app->notifications.len; j++) {
                        if (((lmux_notification *)app->notifications.items[j])->waiting) {
                            any_waiting = true;
                            break;
                        }
                    }

                    ws->waiting = any_waiting;
                }
            }
            return;
        }
    }
}

/* ------------------------------------------------------------------ */
/* Notification rings                                                  */
/* ------------------------------------------------------------------ */

bool lmux_notification_ring_add(lmux_app *app, lmux_id pane_id, const char *reason) {
    if (!app || !pane_id) return false;

    /* Check if ring already exists */
    for (size_t i = 0; i < app->notification_rings.len; i++) {
        lmux_notification_ring *r = app->notification_rings.items[i];
        if (r->pane_id == pane_id) {
            r->active = true;
            r->activated_at = time(NULL);
            if (reason) strncpy(r->reason, reason, sizeof(r->reason) - 1);
            return true;
        }
    }

    /* Create new ring */
    lmux_notification_ring *ring = calloc(1, sizeof(*ring));
    if (!ring) return false;
    ring->pane_id = pane_id;
    ring->active = true;
    ring->activated_at = time(NULL);
    if (reason) strncpy(ring->reason, reason, sizeof(ring->reason) - 1);

    if (app->notification_rings.len == app->notification_rings.cap) {
        app->notification_rings.cap = app->notification_rings.cap ? app->notification_rings.cap * 2 : VEC_GROW;
        app->notification_rings.items = realloc(app->notification_rings.items,
            app->notification_rings.cap * sizeof(void *));
    }
    app->notification_rings.items[app->notification_rings.len++] = ring;
    return true;
}

bool lmux_notification_ring_clear(lmux_app *app, lmux_id pane_id) {
    if (!app) return false;
    for (size_t i = 0; i < app->notification_rings.len; i++) {
        lmux_notification_ring *r = app->notification_rings.items[i];
        if (r->pane_id == pane_id) {
            r->active = false;
            return true;
        }
    }
    return false;
}

size_t lmux_notification_ring_count(lmux_app *app) {
    if (!app) return 0;
    size_t count = 0;
    for (size_t i = 0; i < app->notification_rings.len; i++) {
        if (((lmux_notification_ring *)app->notification_rings.items[i])->active) count++;
    }
    return count;
}

const lmux_notification_ring *lmux_notification_ring_at(lmux_app *app, size_t index) {
    if (!app || index >= app->notification_rings.len) return NULL;
    return app->notification_rings.items[index];
}

/* ------------------------------------------------------------------ */
/* Notification hooks                                                  */
/* ------------------------------------------------------------------ */

int lmux_notification_hook_add(lmux_app *app, const char *event, const char *filter,
                                const char *transform, const char *redirect) {
    if (!app || !event) return -1;

    /* Check if hook already exists for this event */
    for (size_t i = 0; i < app->notification_hooks.len; i++) {
        lmux_notification_hook *h = app->notification_hooks.items[i];
        if (strcmp(h->event, event) == 0) {
            /* Update existing hook */
            if (filter) strncpy(h->filter, filter, sizeof(h->filter) - 1);
            if (transform) strncpy(h->transform, transform, sizeof(h->transform) - 1);
            if (redirect) strncpy(h->redirect, redirect, sizeof(h->redirect) - 1);
            h->enabled = true;
            return (int)i;
        }
    }

    /* Create new hook */
    if (app->notification_hooks.len >= LMUX_MAX_HOOKS) return -1;

    lmux_notification_hook *hook = calloc(1, sizeof(*hook));
    if (!hook) return -1;
    strncpy(hook->event, event, sizeof(hook->event) - 1);
    if (filter) strncpy(hook->filter, filter, sizeof(hook->filter) - 1);
    if (transform) strncpy(hook->transform, transform, sizeof(hook->transform) - 1);
    if (redirect) strncpy(hook->redirect, redirect, sizeof(hook->redirect) - 1);
    hook->enabled = true;

    if (app->notification_hooks.len == app->notification_hooks.cap) {
        app->notification_hooks.cap = app->notification_hooks.cap ? app->notification_hooks.cap * 2 : VEC_GROW;
        app->notification_hooks.items = realloc(app->notification_hooks.items,
            app->notification_hooks.cap * sizeof(void *));
    }
    app->notification_hooks.items[app->notification_hooks.len++] = hook;
    return (int)(app->notification_hooks.len - 1);
}

bool lmux_notification_hook_remove(lmux_app *app, const char *event) {
    if (!app || !event) return false;
    for (size_t i = 0; i < app->notification_hooks.len; i++) {
        lmux_notification_hook *h = app->notification_hooks.items[i];
        if (strcmp(h->event, event) == 0) {
            h->enabled = false;
            return true;
        }
    }
    return false;
}

size_t lmux_notification_hook_count(lmux_app *app) {
    if (!app) return 0;
    return app->notification_hooks.len;
}

const lmux_notification_hook *lmux_notification_hook_at(lmux_app *app, size_t index) {
    if (!app || index >= app->notification_hooks.len) return NULL;
    return app->notification_hooks.items[index];
}

void lmux_desktop_notify(const char *title, const char *body) {
    if (!title || !body) return;
    pid_t pid = fork();
    if (pid == 0) {
        /* Child: exec notify-send */
        execlp("notify-send", "notify-send", title, body, (char *)NULL);
        _exit(1);
    }
    /* Parent: don't wait — fire and forget */
}

/* ------------------------------------------------------------------ */
/* Window management                                                    */
/* ------------------------------------------------------------------ */

lmux_window *lmux_window_create(lmux_app *app, const char *title) {
    if (!app) return NULL;

    lmux_window *w = calloc(1, sizeof(*w));
    if (!w) return NULL;

    w->id = ++app->next_window_id;
    if (title) strncpy(w->title, title, sizeof(w->title) - 1);
    else snprintf(w->title, sizeof(w->title), "Window %zu", app->windows.len + 1);
    w->x = 0;
    w->y = 0;
    w->width = 120;
    w->height = 40;
    w->fullscreen = false;
    w->created_at = time(NULL);
    w->workspace_count = 0;

    if (app->windows.len == app->windows.cap) {
        app->windows.cap = app->windows.cap ? app->windows.cap * 2 : VEC_GROW;
        app->windows.items = realloc(app->windows.items,
            app->windows.cap * sizeof(void *));
    }
    app->windows.items[app->windows.len++] = w;

    lmux_event_push(app, "window.created", "window",
        "{\"window_id\":%u,\"title\":\"%s\"}", w->id, w->title);
    return w;
}

void lmux_window_close(lmux_app *app, lmux_id window_id) {
    if (!app || !window_id) return;
    for (size_t i = 0; i < app->windows.len; i++) {
        lmux_window *w = app->windows.items[i];
        if (w->id == window_id) {
            lmux_event_push(app, "window.closed", "window",
                "{\"window_id\":%u}", window_id);
            free(w);
            /* Remove from array */
            for (size_t j = i; j < app->windows.len - 1; j++) {
                app->windows.items[j] = app->windows.items[j + 1];
            }
            app->windows.len--;
            return;
        }
    }
}

lmux_window *lmux_window_by_id(lmux_app *app, lmux_id id) {
    if (!app || !id) return NULL;
    for (size_t i = 0; i < app->windows.len; i++) {
        lmux_window *w = app->windows.items[i];
        if (w->id == id) return w;
    }
    return NULL;
}

bool lmux_window_focus(lmux_app *app, lmux_id window_id) {
    if (!app || !window_id) return false;
    lmux_window *w = lmux_window_by_id(app, window_id);
    if (!w) return false;
    lmux_event_push(app, "window.focused", "window",
        "{\"window_id\":%u}", window_id);
    return true;
}

bool lmux_window_move_workspace(lmux_app *app, lmux_id window_id, lmux_id workspace_id) {
    if (!app || !window_id || !workspace_id) return false;
    lmux_window *w = lmux_window_by_id(app, window_id);
    if (!w) return false;

    /* Check if already in this window */
    for (size_t i = 0; i < w->workspace_count; i++) {
        if (w->workspace_ids[i] == workspace_id) return true;
    }

    /* Remove from any other window first */
    for (size_t i = 0; i < app->windows.len; i++) {
        lmux_window *other = app->windows.items[i];
        for (size_t j = 0; j < other->workspace_count; j++) {
            if (other->workspace_ids[j] == workspace_id) {
                /* Shift remaining */
                for (size_t k = j; k < other->workspace_count - 1; k++) {
                    other->workspace_ids[k] = other->workspace_ids[k + 1];
                }
                other->workspace_count--;
                break;
            }
        }
    }

    /* Add to target window */
    if (w->workspace_count < 64) {
        w->workspace_ids[w->workspace_count++] = workspace_id;
    }

    lmux_event_push(app, "window.workspace_moved", "window",
        "{\"window_id\":%u,\"workspace_id\":%u}", window_id, workspace_id);
    return true;
}

size_t lmux_window_count(lmux_app *app) {
    if (!app) return 0;
    return app->windows.len;
}

const lmux_window *lmux_window_at(lmux_app *app, size_t index) {
    if (!app || index >= app->windows.len) return NULL;
    return app->windows.items[index];
}

/* ------------------------------------------------------------------ */
/* Persistent SSH PTY                                                   */
/* ------------------------------------------------------------------ */

lmux_ssh_session *lmux_ssh_session_create(lmux_app *app, const char *host,
                                            const char *user, const char *key_path) {
    if (!app || !host) return NULL;

    lmux_ssh_session *s = calloc(1, sizeof(*s));
    if (!s) return NULL;

    s->id = ++app->next_ssh_session_id;
    if (host) strncpy(s->host, host, sizeof(s->host) - 1);
    if (user) strncpy(s->user, user, sizeof(s->user) - 1);
    if (key_path) strncpy(s->key_path, key_path, sizeof(s->key_path) - 1);
    s->ssh_pid = -1;
    s->connected_at = time(NULL);
    s->last_activity = time(NULL);
    s->active = true;

    /* Generate session file path */
    const char *home = getenv("HOME");
    snprintf(s->session_file, sizeof(s->session_file),
        "%s/.local/share/lmux/ssh_session_%u.json",
        home ? home : "/tmp", s->id);

    if (app->ssh_sessions.len == app->ssh_sessions.cap) {
        app->ssh_sessions.cap = app->ssh_sessions.cap ? app->ssh_sessions.cap * 2 : VEC_GROW;
        app->ssh_sessions.items = realloc(app->ssh_sessions.items,
            app->ssh_sessions.cap * sizeof(void *));
    }
    app->ssh_sessions.items[app->ssh_sessions.len++] = s;

    lmux_event_push(app, "ssh.session_created", "ssh",
        "{\"session_id\":%u,\"host\":\"%s\",\"user\":\"%s\"}", s->id, s->host, s->user);
    return s;
}

bool lmux_ssh_session_attach(lmux_ssh_session *s, lmux_id pane_id) {
    if (!s || !pane_id) return false;
    snprintf(s->pane_id_str, sizeof(s->pane_id_str), "%u", pane_id);
    lmux_event_push(NULL, "ssh.session_attached", "ssh",
        "{\"session_id\":%u,\"pane_id\":%u}", s->id, pane_id);
    return true;
}

bool lmux_ssh_session_detach(lmux_app *app, lmux_id session_id) {
    if (!app || !session_id) return false;
    for (size_t i = 0; i < app->ssh_sessions.len; i++) {
        lmux_ssh_session *s = app->ssh_sessions.items[i];
        if (s->id == session_id) {
            s->pane_id_str[0] = 0;
            lmux_event_push(app, "ssh.session_detached", "ssh",
                "{\"session_id\":%u}", session_id);
            return true;
        }
    }
    return false;
}

bool lmux_ssh_session_kill(lmux_app *app, lmux_id session_id) {
    if (!app || !session_id) return false;
    for (size_t i = 0; i < app->ssh_sessions.len; i++) {
        lmux_ssh_session *s = app->ssh_sessions.items[i];
        if (s->id == session_id) {
            if (s->ssh_pid > 0) {
                kill(s->ssh_pid, SIGTERM);
                usleep(100000);  /* 100ms grace period */
                if (kill(s->ssh_pid, 0) == 0) {
                    kill(s->ssh_pid, SIGKILL);
                }
            }
            s->active = false;
            lmux_event_push(app, "ssh.session_killed", "ssh",
                "{\"session_id\":%u}", session_id);
            return true;
        }
    }
    return false;
}

size_t lmux_ssh_session_count(lmux_app *app) {
    if (!app) return 0;
    return app->ssh_sessions.len;
}

const lmux_ssh_session *lmux_ssh_session_at(lmux_app *app, size_t index) {
    if (!app || index >= app->ssh_sessions.len) return NULL;
    return app->ssh_sessions.items[index];
}

bool lmux_ssh_session_save(lmux_app *app, const char *path) {
    if (!app || !path) return false;
    /* Security: validate path to prevent traversal attacks */
    if (!is_safe_path(path, NULL)) {
        lmux_log(LMUX_LOG_WARN, "ssh: session save rejected, path contains traversal: %s", path);
        return false;
    }
    FILE *f = fopen(path, "w");
    if (!f) return false;
    fprintf(f, "{\"sessions\":[");
    for (size_t i = 0; i < app->ssh_sessions.len; i++) {
        lmux_ssh_session *s = app->ssh_sessions.items[i];
        if (i > 0) fprintf(f, ",");
        fprintf(f,
            "{\"id\":%u,\"host\":\"%s\",\"user\":\"%s\",\"key_path\":\"%s\","
            "\"pane_id\":\"%s\",\"active\":%s}",
            s->id, s->host, s->user, s->key_path,
            s->pane_id_str, s->active ? "true" : "false");
    }
    fprintf(f, "]}");
    fclose(f);
    return true;
}

bool lmux_ssh_session_restore(lmux_app *app, const char *path) {
    if (!app || !path) return false;
    /* Security: validate path to prevent traversal attacks */
    if (!is_safe_path(path, NULL)) {
        lmux_log(LMUX_LOG_WARN, "ssh: session restore rejected, path contains traversal: %s", path);
        return false;
    }
    FILE *f = fopen(path, "r");
    if (!f) return false;
    char buf[65536];
    size_t n = fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    if (n == 0) return false;
    buf[n] = 0;

    /* Parse sessions from JSON */
    const char *p = strstr(buf, "\"sessions\":[");
    if (!p) return false;
    p += 12;

    while (*p && *p != ']') {
        while (*p && *p != '{') p++;
        if (*p != '{') break;

        char host[256] = {0}, user[128] = {0}, key_path[512] = {0};
        /* Simple extraction */
        const char *end = strchr(p, '}');
        if (!end) break;
        size_t len = end - p + 1;
        char block[2048] = {0};
        if (len < sizeof block) {
            memcpy(block, p, len);
            block[len] = 0;
            json_extract_string(block, "host", host, sizeof host);
            json_extract_string(block, "user", user, sizeof user);
            json_extract_string(block, "key_path", key_path, sizeof key_path);
        }

        if (host[0]) {
            lmux_ssh_session *s = lmux_ssh_session_create(app, host, user, key_path[0] ? key_path : NULL);
            if (s) {
                char pane_str[64] = {0};
                json_extract_string(block, "pane_id", pane_str, sizeof pane_str);
                if (pane_str[0]) {
                    snprintf(s->pane_id_str, sizeof(s->pane_id_str), "%s", pane_str);
                }
            }
        }

        p = end + 1;
    }
    return true;
}

/* ------------------------------------------------------------------ */
/* Feed Panel                                                           */
/* ------------------------------------------------------------------ */

lmux_feed_panel *lmux_feed_panel_create(lmux_app *app, const char *title, const char *filter) {
    if (!app) return NULL;

    lmux_feed_panel *p = calloc(1, sizeof(*p));
    if (!p) return NULL;

    p->id = ++app->next_feed_panel_id;
    if (title) strncpy(p->title, title, sizeof(p->title) - 1);
    else snprintf(p->title, sizeof(p->title), "Feed %zu", app->feed_panels.len + 1);
    if (filter) strncpy(p->filter, filter, sizeof(p->filter) - 1);
    p->max_entries = 1000;
    p->auto_scroll = true;
    p->created_at = time(NULL);

    if (app->feed_panels.len == app->feed_panels.cap) {
        app->feed_panels.cap = app->feed_panels.cap ? app->feed_panels.cap * 2 : VEC_GROW;
        app->feed_panels.items = realloc(app->feed_panels.items,
            app->feed_panels.cap * sizeof(void *));
    }
    app->feed_panels.items[app->feed_panels.len++] = p;

    lmux_event_push(app, "feed.panel_created", "feed",
        "{\"panel_id\":%u,\"title\":\"%s\"}", p->id, p->title);
    return p;
}

bool lmux_feed_panel_close(lmux_app *app, lmux_id panel_id) {
    if (!app || !panel_id) return false;
    for (size_t i = 0; i < app->feed_panels.len; i++) {
        lmux_feed_panel *p = app->feed_panels.items[i];
        if (p->id == panel_id) {
            lmux_event_push(app, "feed.panel_closed", "feed",
                "{\"panel_id\":%u}", panel_id);
            free(p);
            for (size_t j = i; j < app->feed_panels.len - 1; j++) {
                app->feed_panels.items[j] = app->feed_panels.items[j + 1];
            }
            app->feed_panels.len--;
            return true;
        }
    }
    return false;
}

lmux_feed_panel *lmux_feed_panel_by_id(lmux_app *app, lmux_id id) {
    if (!app || !id) return NULL;
    for (size_t i = 0; i < app->feed_panels.len; i++) {
        lmux_feed_panel *p = app->feed_panels.items[i];
        if (p->id == id) return p;
    }
    return NULL;
}

size_t lmux_feed_panel_count(lmux_app *app) {
    if (!app) return 0;
    return app->feed_panels.len;
}

const lmux_feed_panel *lmux_feed_panel_at(lmux_app *app, size_t index) {
    if (!app || index >= app->feed_panels.len) return NULL;
    return app->feed_panels.items[index];
}

bool lmux_feed_entry_add(lmux_app *app, lmux_id panel_id, const char *event_type,
                          const char *source, const char *text) {
    if (!app || !panel_id || !text) return false;

    lmux_feed_panel *panel = lmux_feed_panel_by_id(app, panel_id);
    if (!panel) return false;

    lmux_feed_entry *e = calloc(1, sizeof(*e));
    if (!e) return false;

    e->panel_id = panel_id;
    e->seq = app->event_seq + 1;
    if (event_type) strncpy(e->event_type, event_type, sizeof(e->event_type) - 1);
    if (source) strncpy(e->source, source, sizeof(e->source) - 1);
    strncpy(e->text, text, sizeof(e->text) - 1);
    e->timestamp = time(NULL);

    if (app->feed_entries.len == app->feed_entries.cap) {
        app->feed_entries.cap = app->feed_entries.cap ? app->feed_entries.cap * 2 : VEC_GROW;
        app->feed_entries.items = realloc(app->feed_entries.items,
            app->feed_entries.cap * sizeof(void *));
    }
    app->feed_entries.items[app->feed_entries.len++] = e;

    /* Trim old entries if over max */
    if (panel->max_entries > 0) {
        size_t count = 0;
        for (size_t i = 0; i < app->feed_entries.len; i++) {
            lmux_feed_entry *entry = app->feed_entries.items[i];
            if (entry->panel_id == panel_id) count++;
        }
        while (count > panel->max_entries) {
            for (size_t i = 0; i < app->feed_entries.len; i++) {
                lmux_feed_entry *entry = app->feed_entries.items[i];
                if (entry->panel_id == panel_id) {
                    free(entry);
                    for (size_t j = i; j < app->feed_entries.len - 1; j++) {
                        app->feed_entries.items[j] = app->feed_entries.items[j + 1];
                    }
                    app->feed_entries.len--;
                    count--;
                    break;
                }
            }
        }
    }

    return true;
}

size_t lmux_feed_entry_count(lmux_app *app, lmux_id panel_id) {
    if (!app || !panel_id) return 0;
    size_t count = 0;
    for (size_t i = 0; i < app->feed_entries.len; i++) {
        if (((lmux_feed_entry *)app->feed_entries.items[i])->panel_id == panel_id) count++;
    }
    return count;
}

const lmux_feed_entry *lmux_feed_entry_at(lmux_app *app, lmux_id panel_id, size_t index) {
    if (!app || !panel_id) return NULL;
    size_t count = 0;
    for (size_t i = 0; i < app->feed_entries.len; i++) {
        lmux_feed_entry *e = app->feed_entries.items[i];
        if (e->panel_id == panel_id) {
            if (count == index) return e;
            count++;
        }
    }
    return NULL;
}

bool lmux_feed_panel_clear(lmux_app *app, lmux_id panel_id) {
    if (!app || !panel_id) return false;
    for (size_t i = app->feed_entries.len; i > 0; i--) {
        lmux_feed_entry *e = app->feed_entries.items[i - 1];
        if (e->panel_id == panel_id) {
            free(e);
            for (size_t j = i - 1; j < app->feed_entries.len - 1; j++) {
                app->feed_entries.items[j] = app->feed_entries.items[j + 1];
            }
            app->feed_entries.len--;
        }
    }
    return true;
}

/* ------------------------------------------------------------------ */
/* Event queue                                                         */
#define LMUX_EVENT_REPLAY_MAX 1024
/* ------------------------------------------------------------------ */

void lmux_event_push(lmux_app *app, const char *name, const char *category, const char *body_fmt, ...) {
    if (!app || !name) return;
    pthread_mutex_lock(&app->event_lock);
    app->event_seq++;
    uint64_t seq = app->event_seq;
    pthread_mutex_unlock(&app->event_lock);

    /* Build data part */
    char data[2048] = {0};
    if (body_fmt) {
        va_list ap;
        va_start(ap, body_fmt);
        vsnprintf(data, sizeof data, body_fmt, ap);
        va_end(ap);
    }

    /* ISO-8601 timestamp */
    char ts[32];
    time_t now = time(NULL);
    struct tm *tm = localtime(&now);
    strftime(ts, sizeof ts, "%Y-%m-%dT%H:%M:%S", tm);

    /* Build event JSON (single allocation for queue) */
    char event_json[4096];
    int n = snprintf(event_json, sizeof event_json,
        "{\"type\":\"event\",\"seq\":%llu,\"boot_id\":\"%s\","
        "\"name\":\"%s\",\"category\":\"%s\","
        "\"occurred_at\":\"%s\"",
        (unsigned long long)seq, app->boot_id,
        name, category, ts);
    if (data[0]) {
        n += snprintf(event_json + n, sizeof event_json - n,
            ",\"data\":{%s}", data);
    }
    snprintf(event_json + n, sizeof event_json - n, "}");

    /* M2 fix: single strdup for queue; replay gets its own copy only
     * because replay persists after queue items are freed. */
    char *ev = strdup(event_json);
    if (!ev) return;

    /* Collect disk writes to do outside the lock (C2 fix) */
    char *log_line = NULL;
    bool need_rotate = false;

    pthread_mutex_lock(&app->event_lock);
    /* Push to live queue */
    vec_push(&app->event_queue, ev);
    /* Push to replay buffer */
    if (app->event_replay.len >= LMUX_EVENT_REPLAY_MAX) {
        free(app->event_replay.items[0]);
        vec_remove(&app->event_replay, 0);
    }
    vec_push(&app->event_replay, strdup(event_json));
    /* Prepare log write outside lock (C2 fix): just set a flag */
    if (app->event_log_fp) {
        log_line = strdup(event_json);
        /* L3 fix: track bytes incrementally instead of ftell */
        if (app->event_log_bytes > 16 * 1024 * 1024) {
            need_rotate = true;
        }
    }
    pthread_mutex_unlock(&app->event_lock);

    /* Disk I/O happens OUTSIDE event_lock (C2 fix) — but with proper synchronization */
    if (log_line) {
        if (need_rotate && app->event_log_fp) {
            fclose(app->event_log_fp);
            char old_path[520];
            snprintf(old_path, sizeof old_path, "%s.1", app->event_log_path);
            rename(app->event_log_path, old_path);
            app->event_log_fp = fopen(app->event_log_path, "a");
            app->event_log_bytes = 0;
        }
        if (app->event_log_fp) {
            int written = fprintf(app->event_log_fp, "%s\n", log_line);
            if (written > 0) {
                pthread_mutex_lock(&app->event_lock);
                app->event_log_bytes += written;
                pthread_mutex_unlock(&app->event_lock);
            }
            fflush(app->event_log_fp);
        }
        free(log_line);
    }
}

size_t lmux_events_pending(lmux_app *app) {
    if (!app) return 0;
    pthread_mutex_lock(&app->event_lock);
    size_t n = app->event_queue.len;
    pthread_mutex_unlock(&app->event_lock);
    return n;
}

char *lmux_event_pop(lmux_app *app) {
    if (!app || app->event_queue.len == 0) return NULL;
    pthread_mutex_lock(&app->event_lock);
    if (app->event_queue.len == 0) {
        pthread_mutex_unlock(&app->event_lock);
        return NULL;
    }
    char *ev = app->event_queue.items[0];
    vec_remove(&app->event_queue, 0);
    pthread_mutex_unlock(&app->event_lock);
    return ev;
}

/* ------------------------------------------------------------------ */
/* Workspace refresh                                                   */
/* ------------------------------------------------------------------ */

/* Read a line from a FILE stream into a fixed buffer. Returns buf on success, NULL on EOF/error. */
static char *read_line(FILE *f, char *buf, size_t cap) {
    if (!fgets(buf, (int)cap, f)) return NULL;
    size_t n = strlen(buf);
    while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == '\r')) buf[--n] = 0;
    return buf;
}

void lmux_workspace_refresh(lmux_workspace *ws) {
    if (!ws || !ws->cwd[0]) return;

    /* Validate cwd to prevent shell injection: only allow safe characters */
    for (const char *p = ws->cwd; *p; p++) {
        unsigned char c = (unsigned char)*p;
        /* Allow: alphanumeric, /, ., _, -, ~, space (for paths) */
        if (c >= 'a' && c <= 'z') continue;
        if (c >= 'A' && c <= 'Z') continue;
        if (c >= '0' && c <= '9') continue;
        if (c == '/' || c == '.' || c == '_' || c == '-' || c == '~') continue;
        if (c == ' ' || c == '\t') continue; /* allow spaces in paths */
        /* Reject everything else */
        lmux_log(LMUX_LOG_WARN, "refresh: cwd contains unsafe character '%c', skipping", c);
        return;
    }

    /* H1 fix: batch git branch + PR into a single shell invocation.
     * Output format: "branch\npr_number_or_empty" */
    char git_cmd[1280];
    snprintf(git_cmd, sizeof git_cmd,
             "cd '%s' && git rev-parse --abbrev-ref HEAD 2>/dev/null && "
             "git rev-parse --abbrev-ref HEAD 2>/dev/null | "
             "xargs -I{} gh pr view --json number --jq '.number // empty' --head {} 2>/dev/null",
             ws->cwd);
    FILE *f = popen(git_cmd, "r");
    if (f) {
        char branch[128] = {0};
        char pr_num[64] = {0};
        /* Read branch (first line) */
        if (fgets(branch, sizeof branch, f)) {
            /* Strip trailing newline */
            size_t len = strlen(branch);
            while (len > 0 && (branch[len-1] == '\n' || branch[len-1] == '\r')) branch[--len] = 0;
            if (len > 0) snprintf(ws->git_branch, sizeof ws->git_branch, "%s", branch);
        }
        /* Read PR number (second line) */
        if (fgets(pr_num, sizeof pr_num, f)) {
            size_t len = strlen(pr_num);
            while (len > 0 && (pr_num[len-1] == '\n' || pr_num[len-1] == '\r')) pr_num[--len] = 0;
            if (len > 0) snprintf(ws->pr_label, sizeof ws->pr_label, "PR #%.57s", pr_num);
            else ws->pr_label[0] = 0;
        } else {
            ws->pr_label[0] = 0;
        }
        pclose(f);
    }

    /* Listening ports via /proc/net/tcp (state 0x0A = LISTEN). */
    char ports_buf[256] = {0};
    FILE *tcp_f = fopen("/proc/net/tcp", "r");
    if (tcp_f) {
        char line[512];
        bool first = true;
        /* Skip header */
        if (read_line(tcp_f, line, sizeof line)) {
            while (read_line(tcp_f, line, sizeof line)) {
                char *save = NULL;
                strtok_r(line, " \t", &save);
                char *la = strtok_r(NULL, " \t", &save);
                if (!la) continue;
                char *colon = strchr(la, ':');
                if (!colon) continue;
                unsigned int sport = (unsigned int)strtoul(colon + 1, NULL, 16);
                strtok_r(NULL, " \t", &save);
                char *st = strtok_r(NULL, " \t", &save);
                if (!st) continue;
                unsigned int state = (unsigned int)strtoul(st, NULL, 16);
                if (state == 0x0A) {
                    if (!first) strncat(ports_buf, ",", sizeof ports_buf - strlen(ports_buf) - 1);
                    char p[16];
                    snprintf(p, sizeof p, "%u", sport);
                    strncat(ports_buf, p, sizeof ports_buf - strlen(ports_buf) - 1);
                    first = false;
                }
            }
        }
        fclose(tcp_f);
    }
    if (ports_buf[0]) {
        snprintf(ws->ports, sizeof ws->ports, "%s", ports_buf);
    }
}

/* ------------------------------------------------------------------ */
/* Socket path                                                         */
/* ------------------------------------------------------------------ */

const char *lmux_socket_path(const lmux_app *app) {
    return app ? app->socket_path : NULL;
}

/* ------------------------------------------------------------------ */
/* JSON dispatch                                                       */
/* ------------------------------------------------------------------ */


/* Find a JSON key's value start after '"key":'. Returns pointer after '"', or NULL. */
static const char *json_find_key(const char *json, const char *key) {
    if (!json || !key) return NULL;
    size_t klen = strlen(key);
    const char *p = json;
    while ((p = strstr(p, key)) != NULL) {
        /* Must be preceded by '"' and followed by '":' */
        if ((p == json || *(p - 1) == '"') && p[klen] == '"' && p[klen + 1] == ':') {
            p += klen + 2; /* skip key": */
            while (*p == ' ' || *p == '\t') p++;
            return p;
        }
        p++;
    }
    return NULL;
}

/* Extract a JSON string value. Fills out with the unescaped value. Returns true on success. */
static bool json_extract_string(const char *json, const char *key, char *out, size_t cap) {
    const char *v = json_find_key(json, key);
    if (!v || *v != '"') return false;
    v++; /* skip opening quote */
    size_t n = 0;
    while (*v && n < cap - 1) {
        if (*v == '"') { v++; break; }
        if (*v == '\\') {
            v++;
            switch (*v) {
            case '"':  out[n++] = '"';  break;
            case '\\': out[n++] = '\\'; break;
            case 'n':  out[n++] = '\n'; break;
            case 'r':  out[n++] = '\r'; break;
            case 't':  out[n++] = '\t'; break;
            default:   out[n++] = *v;   break;
            }
        } else {
            out[n++] = *v;
        }
        v++;
    }
    out[n] = 0;
    return true;
}

/* Extract a JSON boolean value */
static bool json_extract_bool(const char *json, const char *key, bool *out) {
    const char *v = json_find_key(json, key);
    if (!v) return false;
    if (strncmp(v, "true", 4) == 0) { *out = true; return true; }
    if (strncmp(v, "false", 5) == 0) { *out = false; return true; }
    return false;
}

/* Escape a string into a fixed buffer for safe JSON string output
 * (without surrounding quotes). Truncates if output exceeds cap.
 * Handles: ", \\, \\n, \\r, \\t, \\b, \\f, and control chars < 0x20.
 * Returns `out` for convenience. */
static char *json_escape_str(char *out, size_t cap, const char *in) {
    if (!out || cap == 0) return out;
    if (!in) { out[0] = 0; return out; }
    size_t j = 0;
    for (const char *p = in; *p && j + 6 < cap; p++) {
        unsigned char c = (unsigned char)*p;
        switch (c) {
        case '"':  out[j++] = '\\'; out[j++] = '"';  break;
        case '\\': out[j++] = '\\'; out[j++] = '\\'; break;
        case '\n': out[j++] = '\\'; out[j++] = 'n';  break;
        case '\r': out[j++] = '\\'; out[j++] = 'r';  break;
        case '\t': out[j++] = '\\'; out[j++] = 't';  break;
        case '\b': out[j++] = '\\'; out[j++] = 'b';  break;
        case '\f': out[j++] = '\\'; out[j++] = 'f';  break;
        default:
            if (c < 0x20) {
                j += (size_t)snprintf(out + j, cap - j, "\\u%04x", c);
            } else {
                out[j++] = (char)c;
            }
        }
    }
    out[j] = 0;
    return out;
}

/* Simple string hash for deterministic pseudo-random values */
static unsigned hash_string(const char *s) {
    unsigned h = 5381;
    while (*s) h = h * 33 + (unsigned char)*s++;
    return h;
}

/* Dispatch a JSON command string and return a JSON response string (caller must free). */
static char *dispatch_command(lmux_app *app, const char *cmd, const char *args_json) {
    size_t result_cap = 16384;
    char *result = malloc(result_cap);
    int written = 0;
    if (!result) return strdup("{\"ok\":false,\"error\":{\"code\":\"oom\",\"message\":\"out of memory\"}}");
    /* Grow result buffer when nearing capacity */
#define RESULT_GROW do { \
    if ((size_t)written + 1024 >= result_cap) { \
        result_cap = (size_t)written * 2 + 4096; \
        char *_tmp = realloc(result, result_cap); \
        if (!_tmp) { free(result); return strdup("{\"ok\":false,\"error\":{\"code\":\"oom\",\"message\":\"out of memory\"}}"); } \
        result = _tmp; \
    } \
} while(0)
    /* Fixed-size stack buffer for JSON escaping output strings.
     * Large enough for titles, paths, branch names. */
    char _esc_buf[1024];
    /* Escape `in` into _esc_buf and return _esc_buf. Not reentrant —
     * only use as a direct snprintf argument (consumed before next use). */
    #define JESC(in) (json_escape_str(_esc_buf, sizeof _esc_buf, (in)))

    /* --- workspace.create --- */
    if (strcmp(cmd, "workspace.create") == 0) {
        char title[256] = "Workspace";
        json_extract_string(args_json, "title", title, sizeof title);
        lmux_workspace *ws = lmux_workspace_create(app, title);
        if (ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"id\":%u,\"title\":\"%s\"}}",
                ws->id, JESC(ws->title));
            lmux_event_push(app, "workspace.created", "workspace", "\"id\":%u,\"title\":\"%s\"", ws->id, ws->title);
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"create_failed\",\"message\":\"failed to create workspace\"}}");
        }
        goto done;
    }

    /* --- workspace.list --- */
    if (strcmp(cmd, "workspace.list") == 0) {
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{\"workspaces\":[");
        for (size_t i = 0; i < app->workspaces.len; i++) {
            lmux_workspace *ws = app->workspaces.items[i];
            RESULT_GROW;
            char _te[512], _ce[1024], _be[256];
            if (i > 0) written += snprintf(result + written, result_cap - written, ",");
            written += snprintf(result + written, result_cap - written,
                "{\"id\":%u,\"title\":\"%s\",\"cwd\":\"%s\",\"git_branch\":\"%s\"}",
                ws->id,
                json_escape_str(_te, sizeof _te, ws->title),
                json_escape_str(_ce, sizeof _ce, ws->cwd),
                json_escape_str(_be, sizeof _be, ws->git_branch));
        }
        written += snprintf(result + written, result_cap - written, "]}}");
        goto done;
    }

    /* --- workspace.close --- */
    if (strcmp(cmd, "workspace.close") == 0) {
        char id_str[64] = {0};
        json_extract_string(args_json, "id", id_str, sizeof id_str);
        if (id_str[0]) {
            lmux_id wid = (lmux_id)atol(id_str);
            lmux_workspace *ws = lmux_workspace_by_id(app, wid);
            if (ws) {
            lmux_event_push(app, "workspace.closed", "workspace", "\"id\":%u", wid);
                lmux_workspace_close(app, ws);
                written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
            } else {
                written = snprintf(result, result_cap,
                    "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"workspace not found\"}}");
            }
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing id\"}}");
        }
        goto done;
    }

    /* --- workspace.select --- */
    if (strcmp(cmd, "workspace.select") == 0) {
        char id_str[64] = {0};
        json_extract_string(args_json, "id", id_str, sizeof id_str);
        if (id_str[0]) {
            lmux_id wid = (lmux_id)atol(id_str);
            lmux_workspace *ws = lmux_workspace_by_id(app, wid);
            if (ws) {
                for (size_t i = 0; i < app->workspaces.len; i++) {
                    ((lmux_workspace *)app->workspaces.items[i])->focused = false;
                }
                app->ws_last = app->ws_current;
                app->ws_current = ws;
                ws->focused = true;
                written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
            } else {
                written = snprintf(result, result_cap,
                    "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"workspace not found\"}}");
            }
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing id\"}}");
        }
        goto done;
    }

    /* --- workspace.current --- */
    if (strcmp(cmd, "workspace.current") == 0) {
        lmux_workspace *ws = lmux_workspace_focused(app);
        if (ws) {
            char _te[512], _ce[1024], _be[256];
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"id\":%u,\"title\":\"%s\",\"cwd\":\"%s\",\"git_branch\":\"%s\"}}",
                ws->id,
                json_escape_str(_te, sizeof _te, ws->title),
                json_escape_str(_ce, sizeof _ce, ws->cwd),
                json_escape_str(_be, sizeof _be, ws->git_branch));
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no workspaces\"}}");
        }
        goto done;
    }

    /* --- workspace.rename --- */
    if (strcmp(cmd, "workspace.rename") == 0) {
        char id_str[64] = {0}, title[256] = {0};
        json_extract_string(args_json, "id", id_str, sizeof id_str);
        json_extract_string(args_json, "title", title, sizeof title);
        if (id_str[0] && title[0]) {
            lmux_id wid = (lmux_id)atol(id_str);
            lmux_workspace *ws = lmux_workspace_by_id(app, wid);
            if (ws) {
                lmux_workspace_set_title(ws, title);
                written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
            } else {
                written = snprintf(result, result_cap,
                    "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"workspace not found\"}}");
            }
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing id and/or title\"}}");
        }
        goto done;
    }

    /* --- surface.create --- */
    if (strcmp(cmd, "surface.create") == 0) {
        char ws_id[64] = {0}, title[128] = {0};
        json_extract_string(args_json, "workspace_id", ws_id, sizeof ws_id);
        json_extract_string(args_json, "title", title, sizeof title);
        lmux_workspace *ws = ws_id[0] ? lmux_workspace_by_id(app, (lmux_id)atol(ws_id))
                                      : lmux_workspace_focused(app);
        if (ws) {
            lmux_surface *s = lmux_surface_create(ws, title[0] ? title : NULL);
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"id\":%u,\"title\":\"%s\"}}",
                s->id, JESC(s->title));
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"workspace not found\"}}");
        }
        goto done;
    }

    /* --- surface.list --- */
    if (strcmp(cmd, "surface.list") == 0) {
        char ws_id[64] = {0};
        json_extract_string(args_json, "workspace_id", ws_id, sizeof ws_id);
        lmux_workspace *ws = ws_id[0] ? lmux_workspace_by_id(app, (lmux_id)atol(ws_id))
                                      : lmux_workspace_focused(app);
        if (ws) {
            written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{\"surfaces\":[");
            for (size_t i = 0; i < ws->surfaces.len; i++) {
                lmux_surface *s = ws->surfaces.items[i];
                RESULT_GROW;
                char _se[512];
                if (i > 0) written += snprintf(result + written, result_cap - written, ",");
                written += snprintf(result + written, result_cap - written,
                    "{\"id\":%u,\"title\":\"%s\",\"focused\":%s}",
                    s->id, json_escape_str(_se, sizeof _se, s->title), s->focused ? "true" : "false");
            }
            written += snprintf(result + written, result_cap - written, "]}}");
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no workspaces\"}}");
        }
        goto done;
    }

    /* --- surface.close --- */
    if (strcmp(cmd, "surface.close") == 0) {
        char ws_id[64] = {0}, s_id[64] = {0};
        json_extract_string(args_json, "workspace_id", ws_id, sizeof ws_id);
        json_extract_string(args_json, "id", s_id, sizeof s_id);
        lmux_workspace *ws = ws_id[0] ? lmux_workspace_by_id(app, (lmux_id)atol(ws_id))
                                      : lmux_workspace_focused(app);
        if (ws && s_id[0]) {
            lmux_id sid = (lmux_id)atol(s_id);
            for (size_t i = 0; i < ws->surfaces.len; i++) {
                lmux_surface *s = ws->surfaces.items[i];
                if (s->id == sid) {
                    lmux_surface_close(ws, s);
                    written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
                    goto done;
                }
            }
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"surface not found\"}}");
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing id\"}}");
        }
        goto done;
    }

    /* --- surface.split (pane.split) --- */
    if (strcmp(cmd, "surface.split") == 0) {
        char ws_id[64] = {0}, s_id[64] = {0}, dir_str[16] = {0}, command[512] = {0};
        json_extract_string(args_json, "workspace_id", ws_id, sizeof ws_id);
        json_extract_string(args_json, "surface_id", s_id, sizeof s_id);
        json_extract_string(args_json, "direction", dir_str, sizeof dir_str);
        json_extract_string(args_json, "command", command, sizeof command);
        lmux_workspace *ws = ws_id[0] ? lmux_workspace_by_id(app, (lmux_id)atol(ws_id))
                                      : lmux_workspace_focused(app);
        if (!ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"workspace not found\"}}");
            goto done;
        }
        lmux_surface *s = NULL;
        if (s_id[0]) {
            lmux_id sid = (lmux_id)atol(s_id);
            for (size_t i = 0; i < ws->surfaces.len; i++) {
                if (((lmux_surface *)ws->surfaces.items[i])->id == sid) {
                    s = ws->surfaces.items[i];
                    break;
                }
            }
        } else {
            s = lmux_surface_focused(ws);
        }
        if (!s) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"surface not found\"}}");
            goto done;
        }
        lmux_split_dir dir = LMUX_SPLIT_HORIZONTAL;
        if (strcmp(dir_str, "vertical") == 0 || strcmp(dir_str, "v") == 0) {
            dir = LMUX_SPLIT_VERTICAL;
        }
        lmux_pane *p = lmux_pane_split(ws, s, dir, command[0] ? command : NULL, true);
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"id\":%u,\"kind\":\"%s\"}}",
            p->id, p->kind);
        goto done;
    }

    /* --- pane.list --- */
    if (strcmp(cmd, "pane.list") == 0) {
        char ws_id[64] = {0};
        json_extract_string(args_json, "workspace_id", ws_id, sizeof ws_id);
        lmux_workspace *ws = ws_id[0] ? lmux_workspace_by_id(app, (lmux_id)atol(ws_id))
                                      : lmux_workspace_focused(app);
        if (ws) {
            written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{\"panes\":[");
            bool first = true;
            for (size_t i = 0; i < ws->surfaces.len; i++) {
                lmux_surface *s = ws->surfaces.items[i];
                for (size_t j = 0; j < s->panes.len; j++) {
                    lmux_pane *p = s->panes.items[j];
                    RESULT_GROW;
                    if (!first) written += snprintf(result + written, result_cap - written, ",");
                    written += snprintf(result + written, result_cap - written,
                        "{\"id\":%u,\"kind\":\"%s\",\"focused\":%s,\"surface_id\":%u}",
                        p->id, p->kind, p->focused ? "true" : "false", s->id);
                    first = false;
                }
            }
            written += snprintf(result + written, result_cap - written, "]}}");
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no workspaces\"}}");
        }
        goto done;
    }

    /* --- pane.focus --- */
    if (strcmp(cmd, "pane.focus") == 0) {
        char ws_id[64] = {0}, p_id[64] = {0};
        json_extract_string(args_json, "workspace_id", ws_id, sizeof ws_id);
        json_extract_string(args_json, "id", p_id, sizeof p_id);
        lmux_workspace *ws = ws_id[0] ? lmux_workspace_by_id(app, (lmux_id)atol(ws_id))
                                      : lmux_workspace_focused(app);
        if (ws && p_id[0]) {
            lmux_id pid = (lmux_id)atol(p_id);
            for (size_t i = 0; i < ws->surfaces.len; i++) {
                lmux_surface *s = ws->surfaces.items[i];
                for (size_t j = 0; j < s->panes.len; j++) {
                    lmux_pane *p = s->panes.items[j];
                    if (p->id == pid) {
                        lmux_pane_focus(ws, p);
                        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
                        goto done;
                    }
                }
            }
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"pane not found\"}}");
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing id\"}}");
        }
        goto done;
    }

    /* --- pane.resize --- */
    if (strcmp(cmd, "pane.resize") == 0) {
        char ws_id[64] = {0}, s_id[64] = {0}, dir_str[16] = {0};
        char cols_str[32] = {0}, rows_str[32] = {0};
        json_extract_string(args_json, "workspace_id", ws_id, sizeof ws_id);
        json_extract_string(args_json, "surface_id", s_id, sizeof s_id);
        json_extract_string(args_json, "direction", dir_str, sizeof dir_str);
        json_extract_string(args_json, "cols", cols_str, sizeof cols_str);
        json_extract_string(args_json, "rows", rows_str, sizeof rows_str);
        lmux_workspace *ws = ws_id[0] ? lmux_workspace_by_id(app, (lmux_id)atol(ws_id))
                                      : lmux_workspace_focused(app);
        if (!ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"workspace not found\"}}");
            goto done;
        }
        lmux_surface *s = NULL;
        if (s_id[0]) {
            lmux_id sid = (lmux_id)atol(s_id);
            for (size_t i = 0; i < ws->surfaces.len; i++) {
                if (((lmux_surface *)ws->surfaces.items[i])->id == sid) {
                    s = ws->surfaces.items[i];
                    break;
                }
            }
        } else {
            s = lmux_surface_focused(ws);
        }
        if (s) {
            /* Resize focused pane via PTY */
            lmux_pane *p = lmux_pane_focused(ws);
            if (p && cols_str[0] && rows_str[0]) {
                lmux_pane_pty_set_size(p, (int)atol(cols_str), (int)atol(rows_str));
            }
        }
        lmux_event_push(app, "pane.resized", "pane", NULL);
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- pane.focus_dir --- */
    if (strcmp(cmd, "pane.focus_dir") == 0) {
        char dx_str[16] = {0}, dy_str[16] = {0};
        json_extract_string(args_json, "dx", dx_str, sizeof dx_str);
        json_extract_string(args_json, "dy", dy_str, sizeof dy_str);
        if (!dx_str[0] && !dy_str[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing dx and/or dy\"}}");
            goto done;
        }
        lmux_workspace *ws = lmux_workspace_focused(app);
        if (!ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no workspaces\"}}");
            goto done;
        }
        int dx = dx_str[0] ? (int)atol(dx_str) : 0;
        int dy = dy_str[0] ? (int)atol(dy_str) : 0;
        lmux_pane_focus_dir(ws, dx, dy);
        lmux_event_push(app, "pane.focused", "pane", "\"id\":%u", lmux_pane_focused(ws)->id);
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- workspace.refresh --- */
    if (strcmp(cmd, "workspace.refresh") == 0) {
        char ws_id[64] = {0};
        json_extract_string(args_json, "workspace_id", ws_id, sizeof ws_id);
        lmux_workspace *ws = ws_id[0] ? lmux_workspace_by_id(app, (lmux_id)atol(ws_id))
                                      : lmux_workspace_focused(app);
        if (!ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"workspace not found\"}}");
            goto done;
        }
        lmux_workspace_refresh(ws);
        lmux_event_push(app, "workspace.refreshed", "workspace", "\"id\":%u", ws->id);
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- read-screen --- */
    if (strcmp(cmd, "read-screen") == 0 || strcmp(cmd, "read_screen") == 0 || strcmp(cmd, "capture-pane") == 0) {
        char ws_id[64] = {0}, s_id[64] = {0};
        json_extract_string(args_json, "workspace_id", ws_id, sizeof ws_id);
        json_extract_string(args_json, "surface_id", s_id, sizeof s_id);
        lmux_workspace *ws = ws_id[0] ? lmux_workspace_by_id(app, (lmux_id)atol(ws_id))
                                      : lmux_workspace_focused(app);
        if (!ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"workspace not found\"}}");
            goto done;
        }
        lmux_surface *s = NULL;
        if (s_id[0]) {
            lmux_id sid = (lmux_id)atol(s_id);
            for (size_t i = 0; i < ws->surfaces.len; i++) {
                if (((lmux_surface *)ws->surfaces.items[i])->id == sid) {
                    s = ws->surfaces.items[i];
                    break;
                }
            }
        } else {
            s = lmux_surface_focused(ws);
        }
        if (!s) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"surface not found\"}}");
            goto done;
        }
        lmux_pane *p = NULL;
        for (size_t i = 0; i < s->panes.len; i++) {
            lmux_pane *cp = s->panes.items[i];
            if (cp->focused) { p = cp; break; }
        }
        if (!p && s->panes.len > 0) p = s->panes.items[0];
        if (!p || p->pty_fd < 0) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no pty to read\"}}");
            goto done;
        }
        /* Non-blocking read from pty master */
        char screen[2048];
        size_t slen = 0;
        int flags = fcntl(p->pty_fd, F_GETFL, 0);
        fcntl(p->pty_fd, F_SETFL, flags | O_NONBLOCK);
        ssize_t r;
        while (slen < sizeof screen - 1 && (r = read(p->pty_fd, screen + slen, sizeof screen - 1 - slen)) > 0) {
            slen += (size_t)r;
        }
        fcntl(p->pty_fd, F_SETFL, flags);
        screen[slen] = 0;
        /* Escape for JSON */
        char escaped[4096];
        size_t elen = 0;
        for (size_t ii = 0; screen[ii] && elen < sizeof escaped - 6; ii++) {
            char c = screen[ii];
            if (c == '\\' || c == '"') { escaped[elen++] = '\\'; escaped[elen++] = c; }
            else if (c == '\n') { escaped[elen++] = '\\'; escaped[elen++] = 'n'; }
            else if (c == '\r') { escaped[elen++] = '\\'; escaped[elen++] = 'r'; }
            else if (c == '\t') { escaped[elen++] = '\\'; escaped[elen++] = 't'; }
            else if ((unsigned char)c < 32) { /* skip control chars */ }
            else { escaped[elen++] = c; }
        }
        escaped[elen] = 0;
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"text\":\"%s\"}}", escaped);
        goto done;
    }

    /* --- surface.send_text --- */
    if (strcmp(cmd, "surface.send_text") == 0) {
        char ws_id[64] = {0}, s_id[64] = {0}, text[4096] = {0};
        json_extract_string(args_json, "workspace_id", ws_id, sizeof ws_id);
        json_extract_string(args_json, "surface_id", s_id, sizeof s_id);
        json_extract_string(args_json, "text", text, sizeof text);
        lmux_workspace *ws = ws_id[0] ? lmux_workspace_by_id(app, (lmux_id)atol(ws_id))
                                      : lmux_workspace_focused(app);
        if (!ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"workspace not found\"}}");
            goto done;
        }
        lmux_surface *s = NULL;
        if (s_id[0]) {
            lmux_id sid = (lmux_id)atol(s_id);
            for (size_t i = 0; i < ws->surfaces.len; i++) {
                if (((lmux_surface *)ws->surfaces.items[i])->id == sid) {
                    s = ws->surfaces.items[i];
                    break;
                }
            }
        } else {
            s = lmux_surface_focused(ws);
        }
        if (s && text[0]) {
            /* Send to the focused pane in this surface */
            lmux_pane *p = lmux_pane_focused(ws);
            if (p) {
                /* Find the pane belonging to this surface */
                for (size_t i = 0; i < s->panes.len; i++) {
                    lmux_pane *cp = s->panes.items[i];
                    if (cp->focused) { p = cp; break; }
                }
                lmux_pane_send_keys(p, text);
                written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
            } else {
                written = snprintf(result, result_cap,
                    "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no focused pane\"}}");
            }
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing text\"}}");
        }
        goto done;
    }

    /* --- notification.create --- */
    if (strcmp(cmd, "notification.create") == 0) {
        char text[1024] = {0};
        bool waiting = false;
        json_extract_string(args_json, "text", text, sizeof text);
        json_extract_bool(args_json, "waiting", &waiting);
        char ws_id[64] = {0};
        json_extract_string(args_json, "workspace_id", ws_id, sizeof ws_id);
        if (ws_id[0]) {
            lmux_workspace *ws = lmux_workspace_by_id(app, (lmux_id)atol(ws_id));
            if (ws) lmux_workspace_notify(ws, text, waiting);
            else   lmux_notify(app, text, waiting);
        } else {
            lmux_notify(app, text, waiting);
        }
        /* Desktop notification for waiting (agent-input-required) alerts */
        if (waiting) {
            char dtitle[128];
            snprintf(dtitle, sizeof dtitle, "lmux");
            lmux_desktop_notify(dtitle, text);
        }
        lmux_event_push(app, "notification.created", "notification", "\"seq\":%llu,\"text\":\"%s\",\"waiting\":%s",
            (unsigned long long)app->notify_seq, text, waiting ? "true" : "false");
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- notification.list --- */
    if (strcmp(cmd, "notification.list") == 0) {
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{\"notifications\":[");
        for (size_t i = 0; i < app->notifications.len; i++) {
            const lmux_notification *n = app->notifications.items[i];
            RESULT_GROW;
            if (i > 0) written += snprintf(result + written, result_cap - written, ",");
            written += snprintf(result + written, result_cap - written,
                "{\"seq\":%llu,\"text\":\"%s\",\"waiting\":%s,\"created_at\":%lld,\"workspace_id\":\"%s\"}",
                (unsigned long long)n->seq, n->text, n->waiting ? "true" : "false",
                (long long)n->created_at, n->workspace_id);
        }
        written += snprintf(result + written, result_cap - written, "]}}");
        goto done;
    }

    /* --- notification.clear --- */
    if (strcmp(cmd, "notification.clear") == 0) {
        for (size_t i = 0; i < app->notifications.len; i++) {
            free(app->notifications.items[i]);
        }
        app->notifications.len = 0;
        lmux_event_push(app, "notification.cleared", "notification", NULL);
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- notification.mark_read --- */
    if (strcmp(cmd, "notification.mark_read") == 0) {
        char seq_str[64] = {0};
        json_extract_string(args_json, "seq", seq_str, sizeof seq_str);
        if (seq_str[0]) {
            uint64_t seq = (uint64_t)atol(seq_str);
            lmux_notification_mark_read(app, seq);
        } else {
            for (size_t i = 0; i < app->notifications.len; i++) {
                lmux_notification *n = app->notifications.items[i];
                n->waiting = false;
            }
        }
        lmux_event_push(app, "notification.read", "notification", NULL);
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- notification.ring.add --- */
    if (strcmp(cmd, "notification.ring.add") == 0 || strcmp(cmd, "notification.ring") == 0) {
        char pane_id_str[64] = {0}, reason[256] = {0};
        json_extract_string(args_json, "pane_id", pane_id_str, sizeof pane_id_str);
        json_extract_string(args_json, "reason", reason, sizeof reason);
        if (!pane_id_str[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing pane_id\"}}");
            goto done;
        }
        lmux_id pid = (lmux_id)atol(pane_id_str);
        if (lmux_notification_ring_add(app, pid, reason[0] ? reason : NULL)) {
            written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"ring_failed\",\"message\":\"could not add ring\"}}");
        }
        goto done;
    }

    /* --- notification.ring.clear --- */
    if (strcmp(cmd, "notification.ring.clear") == 0) {
        char pane_id_str[64] = {0};
        json_extract_string(args_json, "pane_id", pane_id_str, sizeof pane_id_str);
        if (!pane_id_str[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing pane_id\"}}");
            goto done;
        }
        lmux_id pid = (lmux_id)atol(pane_id_str);
        if (lmux_notification_ring_clear(app, pid)) {
            written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"ring not found\"}}");
        }
        goto done;
    }

    /* --- notification.ring.list --- */
    if (strcmp(cmd, "notification.ring.list") == 0) {
        size_t count = lmux_notification_ring_count(app);
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"count\":%zu}}", count);
        goto done;
    }

    /* --- notification.hooks.add --- */
    if (strcmp(cmd, "notification.hooks.add") == 0 || strcmp(cmd, "notification.hook") == 0) {
        char event[64] = {0}, filter[256] = {0}, transform[512] = {0}, redirect[256] = {0};
        json_extract_string(args_json, "event", event, sizeof event);
        json_extract_string(args_json, "filter", filter, sizeof filter);
        json_extract_string(args_json, "transform", transform, sizeof transform);
        json_extract_string(args_json, "redirect", redirect, sizeof redirect);
        if (!event[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing event\"}}");
            goto done;
        }
        int idx = lmux_notification_hook_add(app, event,
            filter[0] ? filter : NULL,
            transform[0] ? transform : NULL,
            redirect[0] ? redirect : NULL);
        if (idx >= 0) {
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"index\":%d}}", idx);
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"hook_failed\",\"message\":\"could not add hook\"}}");
        }
        goto done;
    }

    /* --- notification.hooks.remove --- */
    if (strcmp(cmd, "notification.hooks.remove") == 0) {
        char event[64] = {0};
        json_extract_string(args_json, "event", event, sizeof event);
        if (!event[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing event\"}}");
            goto done;
        }
        if (lmux_notification_hook_remove(app, event)) {
            written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"hook not found\"}}");
        }
        goto done;
    }

    /* --- notification.hooks.list --- */
    if (strcmp(cmd, "notification.hooks.list") == 0) {
        size_t count = lmux_notification_hook_count(app);
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"count\":%zu}}", count);
        goto done;
    }

    /* --- surface.focus --- */
    if (strcmp(cmd, "surface.focus") == 0) {
        char s_id[64] = {0}, ws_id[64] = {0};
        json_extract_string(args_json, "id", s_id, sizeof s_id);
        json_extract_string(args_json, "workspace_id", ws_id, sizeof ws_id);
        if (!s_id[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing id\"}}");
            goto done;
        }
        lmux_id sid = (lmux_id)atol(s_id);
        lmux_surface *found = NULL;
        lmux_workspace *found_ws = NULL;
        if (ws_id[0]) {
            lmux_workspace *ws = lmux_workspace_by_id(app, (lmux_id)atol(ws_id));
            if (ws) {
                for (size_t i = 0; i < ws->surfaces.len; i++) {
                    if (((lmux_surface *)ws->surfaces.items[i])->id == sid) {
                        found = ws->surfaces.items[i];
                        found_ws = ws;
                        break;
                    }
                }
            }
        } else {
            for (size_t w = 0; w < app->workspaces.len && !found; w++) {
                lmux_workspace *ws = app->workspaces.items[w];
                for (size_t i = 0; i < ws->surfaces.len; i++) {
                    if (((lmux_surface *)ws->surfaces.items[i])->id == sid) {
                        found = ws->surfaces.items[i];
                        found_ws = ws;
                        break;
                    }
                }
            }
        }
        if (!found || !found_ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"surface not found\"}}");
            goto done;
        }
        for (size_t i = 0; i < found_ws->surfaces.len; i++) {
            ((lmux_surface *)found_ws->surfaces.items[i])->focused = false;
        }
        found->focused = true;
        lmux_event_push(app, "surface.focused", "surface", "\"id\":%u", found->id);
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- surface.send_key --- */
    if (strcmp(cmd, "surface.send_key") == 0) {
        char ws_id[64] = {0}, s_id[64] = {0}, text[4096] = {0};
        json_extract_string(args_json, "workspace_id", ws_id, sizeof ws_id);
        json_extract_string(args_json, "surface_id", s_id, sizeof s_id);
        json_extract_string(args_json, "text", text, sizeof text);
        lmux_workspace *ws = ws_id[0] ? lmux_workspace_by_id(app, (lmux_id)atol(ws_id))
                                      : lmux_workspace_focused(app);
        if (!ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"workspace not found\"}}");
            goto done;
        }
        lmux_surface *s = NULL;
        if (s_id[0]) {
            lmux_id sid = (lmux_id)atol(s_id);
            for (size_t i = 0; i < ws->surfaces.len; i++) {
                if (((lmux_surface *)ws->surfaces.items[i])->id == sid) {
                    s = ws->surfaces.items[i];
                    break;
                }
            }
        } else {
            s = lmux_surface_focused(ws);
        }
        if (!s) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"surface not found\"}}");
            goto done;
        }
        if (!text[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing text\"}}");
            goto done;
        }
        lmux_pane *p = NULL;
        for (size_t i = 0; i < s->panes.len; i++) {
            lmux_pane *cp = s->panes.items[i];
            if (cp->focused) { p = cp; break; }
        }
        if (!p && s->panes.len > 0) p = s->panes.items[0];
        if (p) {
            lmux_pane_send_keys(p, text);
            written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no panes\"}}");
        }
        goto done;
    }

    /* --- workspace.reorder --- */
    if (strcmp(cmd, "workspace.reorder") == 0) {
        char from_str[64] = {0}, to_str[64] = {0};
        json_extract_string(args_json, "from_index", from_str, sizeof from_str);
        json_extract_string(args_json, "to_index", to_str, sizeof to_str);
        if (!from_str[0] || !to_str[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing from_index and/or to_index\"}}");
            goto done;
        }
        size_t from = (size_t)atol(from_str);
        size_t to = (size_t)atol(to_str);
        size_t len = app->workspaces.len;
        if (from >= len || to >= len) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"out_of_range\",\"message\":\"index out of range\"}}");
            goto done;
        }
        if (from == to) {
            written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
            goto done;
        }
        void *tmp = app->workspaces.items[from];
        if (from < to) {
            memmove(&app->workspaces.items[from], &app->workspaces.items[from + 1],
                    (to - from) * sizeof(void *));
        } else {
            memmove(&app->workspaces.items[to + 1], &app->workspaces.items[to],
                    (from - to) * sizeof(void *));
        }
        lmux_event_push(app, "workspace.reordered", "workspace", NULL);
        app->workspaces.items[to] = tmp;
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- snapshot.save [path] --- */
    if (strcmp(cmd, "snapshot.save") == 0) {
        char path[1024] = {0};
        json_extract_string(args_json, "path", path, sizeof path);
        if (!path[0]) {
            const char *home = getenv("HOME");
            snprintf(path, sizeof path, "%s/.lmux_snapshot.json", home ? home : "/tmp");
        }
        /* Security: validate path to prevent traversal attacks */
        if (!is_safe_path(path, NULL)) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"path contains traversal sequences\"}}");
            goto done;
        }
        bool ok = lmux_snapshot_save(app, path);
        if (ok) {
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"path\":\"%s\"}}", path);
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"save_failed\",\"message\":\"failed to save snapshot\"}}");
        }
        goto done;
    }

    /* --- snapshot.load [path] --- */
    if (strcmp(cmd, "snapshot.load") == 0) {
        char path[1024] = {0};
        json_extract_string(args_json, "path", path, sizeof path);
        if (!path[0]) {
            const char *home = getenv("HOME");
            snprintf(path, sizeof path, "%s/.lmux_snapshot.json", home ? home : "/tmp");
        }
        /* Security: validate path to prevent traversal attacks */
        if (!is_safe_path(path, NULL)) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"path contains traversal sequences\"}}");
            goto done;
        }
        bool ok = lmux_snapshot_load(app, path);
        if (ok) {
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"path\":\"%s\"}}", path);
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"load_failed\",\"message\":\"failed to load snapshot\"}}");
        }
        goto done;
    }

    /* --- move-surface --- */
    if (strcmp(cmd, "move-surface") == 0 || strcmp(cmd, "move_surface") == 0) {
        char s_id[64] = {0}, target_ws_id[64] = {0}, source_ws_id[64] = {0};
        json_extract_string(args_json, "id", s_id, sizeof s_id);
        json_extract_string(args_json, "target_workspace_id", target_ws_id, sizeof target_ws_id);
        json_extract_string(args_json, "workspace_id", source_ws_id, sizeof source_ws_id);
        if (!s_id[0] || !target_ws_id[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing id and/or target_workspace_id\"}}");
            goto done;
        }
        lmux_id sid = (lmux_id)atol(s_id);
        lmux_workspace *src_ws = source_ws_id[0] ? lmux_workspace_by_id(app, (lmux_id)atol(source_ws_id))
                                                : NULL;
        lmux_workspace *dst_ws = lmux_workspace_by_id(app, (lmux_id)atol(target_ws_id));
        if (!dst_ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"target workspace not found\"}}");
            goto done;
        }
        lmux_surface *found_s = NULL;
        lmux_workspace *found_ws = NULL;
        /* Search workspaces for the surface */
        for (size_t w = 0; w < app->workspaces.len && !found_s; w++) {
            lmux_workspace *cws = app->workspaces.items[w];
            if (src_ws && cws != src_ws) continue;
            for (size_t i = 0; i < cws->surfaces.len; i++) {
                if (((lmux_surface *)cws->surfaces.items[i])->id == sid) {
                    found_s = cws->surfaces.items[i];
                    found_ws = cws;
                    break;
                }
            }
        }
        if (!found_s || !found_ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"surface not found\"}}");
            goto done;
        }
        if (found_ws == dst_ws) {
            written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
            goto done;
        }
        /* Remove from source workspace */
        for (size_t i = 0; i < found_ws->surfaces.len; i++) {
            if (found_ws->surfaces.items[i] == found_s) {
                vec_remove(&found_ws->surfaces, i);
                break;
            }
        }
        /* Add to target workspace */
        vec_push(&dst_ws->surfaces, found_s);
        lmux_event_push(app, "surface.moved", "surface", "\"id\":%u,\"target_ws\":%u", found_s->id, dst_ws->id);
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- surface.rename --- */
    if (strcmp(cmd, "surface.rename") == 0) {
        char s_id[64] = {0}, title[256] = {0}, ws_id[64] = {0};
        json_extract_string(args_json, "id", s_id, sizeof s_id);
        json_extract_string(args_json, "title", title, sizeof title);
        json_extract_string(args_json, "workspace_id", ws_id, sizeof ws_id);
        if (!s_id[0] || !title[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing id and/or title\"}}");
            goto done;
        }
        lmux_id sid = (lmux_id)atol(s_id);
        lmux_surface *found = NULL;
        if (ws_id[0]) {
            lmux_workspace *ws = lmux_workspace_by_id(app, (lmux_id)atol(ws_id));
            if (ws) {
                for (size_t i = 0; i < ws->surfaces.len; i++) {
                    if (((lmux_surface *)ws->surfaces.items[i])->id == sid) {
                        found = ws->surfaces.items[i];
                        break;
                    }
                }
            }
        } else {
            for (size_t w = 0; w < app->workspaces.len && !found; w++) {
                lmux_workspace *ws = app->workspaces.items[w];
                for (size_t i = 0; i < ws->surfaces.len; i++) {
                    if (((lmux_surface *)ws->surfaces.items[i])->id == sid) {
                        found = ws->surfaces.items[i];
                        break;
                    }
                }
            }
        }
        if (!found) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"surface not found\"}}");
            goto done;
        }
        snprintf(found->title, sizeof found->title, "%s", title);
        lmux_event_push(app, "surface.renamed", "surface", "\"id\":%u,\"title\":\"%s\"", found->id, title);
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }
    /* --- tree --- */
    if (strcmp(cmd, "tree") == 0) {
        /* H4 fix: pre-allocate buffer based on workspace/surface/pane counts.
         * Estimated per-entry: ~80 bytes workspace + ~60 bytes surface + ~30 bytes pane. */
        size_t ws_count = app->workspaces.len;
        size_t total_panels = 0;
        for (size_t i = 0; i < ws_count; i++) {
            lmux_workspace *ws = app->workspaces.items[i];
            total_panels += 1 + ws->surfaces.len;
            for (size_t j = 0; j < ws->surfaces.len; j++) {
                total_panels += ((lmux_surface *)ws->surfaces.items[j])->panes.len;
            }
        }
        size_t est = 128 + ws_count * 80 + total_panels * 60;
        if (est > result_cap) {
            result_cap = est;
            char *tmp = realloc(result, result_cap);
            if (tmp) result = tmp;
        }
        /* Build hierarchy: workspaces -> surfaces -> panes */
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{\"windows\":[{\"id\":1,\"workspaces\":[");
        int first_ws = 1;
        for (size_t i = 0; i < ws_count; i++) {
            lmux_workspace *ws = app->workspaces.items[i];
            if (!first_ws) written += snprintf(result + written, result_cap - written, ",");
            first_ws = 0;
            char _we[512];
            written += snprintf(result + written, result_cap - written,
                "{\"id\":%u,\"title\":\"%s\",\"surfaces\":[", ws->id, json_escape_str(_we, sizeof _we, ws->title));
            int first_s = 1;
            for (size_t j = 0; j < ws->surfaces.len; j++) {
                lmux_surface *s = ws->surfaces.items[j];
                if (!first_s) written += snprintf(result + written, result_cap - written, ",");
                first_s = 0;
                char _se[512];
                written += snprintf(result + written, result_cap - written,
                    "{\"id\":%u,\"title\":\"%s\",\"panes\":[", s->id, json_escape_str(_se, sizeof _se, s->title));
                int first_p = 1;
                for (size_t k = 0; k < s->panes.len; k++) {
                    lmux_pane *p = s->panes.items[k];
                    if (!first_p) written += snprintf(result + written, result_cap - written, ",");
                    first_p = 0;
                    written += snprintf(result + written, result_cap - written,
                        "{\"id\":%u,\"pty_pid\":%d}", p->id, (int)p->child_pid);
                }
                written += snprintf(result + written, result_cap - written, "]}");
            }
            written += snprintf(result + written, result_cap - written, "]}");
        }
        written += snprintf(result + written, result_cap - written, "]}]}}");
        goto done;
    }

    /* --- ssh --- */
    if (strcmp(cmd, "ssh") == 0) {
        char host[256] = {0}, port_str[16] = {0};
        json_extract_string(args_json, "host", host, sizeof host);
        json_extract_string(args_json, "port", port_str, sizeof port_str);

        if (!host[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing host\"}}");
            goto done;
        }

        int port = port_str[0] ? (int)atol(port_str) : 22;
        lmux_workspace *ws = lmux_workspace_ssh_create(app, host, port);

        if (!ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"create_failed\",\"message\":\"failed to create SSH workspace\"}}");
            goto done;
        }
        char _te[512], _he[512];
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"workspace_id\":%u,\"title\":\"%s\",\"command\":\"ssh %s\"}}",
            ws->id, json_escape_str(_te, sizeof _te, ws->title), json_escape_str(_he, sizeof _he, host));
        goto done;
    }

    /* --- display-message --- */
    if (strcmp(cmd, "display-message") == 0 || strcmp(cmd, "display_message") == 0) {
        char msg[1024] = {0};
        json_extract_string(args_json, "message", msg, sizeof msg);
        char _me[2048];
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"message\":\"%s\"}}", json_escape_str(_me, sizeof _me, msg));
        goto done;
    }

    /* --- last-pane --- */
    if (strcmp(cmd, "last-pane") == 0 || strcmp(cmd, "last_pane") == 0) {
        if (app->last_focused) {
            lmux_pane *p = app->last_focused;
            for (size_t i = 0; i < app->workspaces.len; i++) {
                lmux_workspace *ws = app->workspaces.items[i];
                for (size_t j = 0; j < ws->surfaces.len; j++) {
                    lmux_surface *s = ws->surfaces.items[j];
                    for (size_t k = 0; k < s->panes.len; k++) {
                        if (s->panes.items[k] == p) {
                            app->ws_current = ws;
                            app->ws_last = app->ws_current;
                            /* Focus the surface this pane belongs to */
                            for (size_t si = 0; si < ws->surfaces.len; si++) {
                                ((lmux_surface *)ws->surfaces.items[si])->focused = false;
                            }
                            s->focused = true;
                            lmux_pane_focus(ws, p);
                            written = snprintf(result, result_cap,
                                "{\"ok\":true,\"result\":{\"pane_id\":%u}}", p->id);
                            goto done;
                        }
                    }
                }
            }
        }
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- next-window --- */
    if (strcmp(cmd, "next-window") == 0 || strcmp(cmd, "next_window") == 0) {
        if (app->workspaces.len > 0) {
            size_t idx = 0;
            for (size_t i = 0; i < app->workspaces.len; i++) {
                if (app->workspaces.items[i] == app->ws_current) {
                    idx = (i + 1) % app->workspaces.len;
                    break;
                }
            }
            for (size_t i = 0; i < app->workspaces.len; i++) {
                ((lmux_workspace *)app->workspaces.items[i])->focused = false;
            }
            app->ws_last = app->ws_current;
            app->ws_current = app->workspaces.items[idx];
            app->ws_current->focused = true;
        }
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- previous-window --- */
    if (strcmp(cmd, "previous-window") == 0 || strcmp(cmd, "previous_window") == 0) {
        if (app->workspaces.len > 0) {
            size_t idx = 0;
            for (size_t i = 0; i < app->workspaces.len; i++) {
                if (app->workspaces.items[i] == app->ws_current) {
                    idx = (i + app->workspaces.len - 1) % app->workspaces.len;
                    break;
                }
            }
            for (size_t i = 0; i < app->workspaces.len; i++) {
                ((lmux_workspace *)app->workspaces.items[i])->focused = false;
            }
            app->ws_last = app->ws_current;
            app->ws_current = app->workspaces.items[idx];
            app->ws_current->focused = true;
        }
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- last-window --- */
    if (strcmp(cmd, "last-window") == 0 || strcmp(cmd, "last_window") == 0) {
        if (app->ws_last) {
            for (size_t i = 0; i < app->workspaces.len; i++) {
                ((lmux_workspace *)app->workspaces.items[i])->focused = false;
            }
            app->ws_current = app->ws_last;
            app->ws_current->focused = true;
            app->ws_last = NULL;
        }
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }
    /* --- ping --- */
    if (strcmp(cmd, "ping") == 0) {
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"version\":\"%s\"}}", LMUX_VERSION);
        goto done;
    }

    /* --- health.live --- Process is responding. */
    if (strcmp(cmd, "health.live") == 0) {
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"status\":\"alive\",\"pid\":%d,\"uptime_seconds\":%ld}}",
            (int)getpid(), (long)(time(NULL) - app->start_time));
        goto done;
    }

    /* --- health.ready --- All dependencies healthy. */
    if (strcmp(cmd, "health.ready") == 0) {
        /* Check: app running, workspaces accessible, server listening. */
        bool ready = app->running && app->listen_fd >= 0;
        written = snprintf(result, result_cap,
            "{\"ok\":%s,\"result\":{\"status\":\"%s\",\"workspaces\":%zu,\"surfaces\":%zu}}",
            ready ? "true" : "false",
            ready ? "ready" : "not_ready",
            app->workspaces.len,
            (size_t)0);  /* surface count summed below if needed */
        goto done;
    }

    /* --- metrics --- Server metrics (Prometheus-style). */
    if (strcmp(cmd, "metrics") == 0) {
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{"
            "\"requests\":%llu,\"errors\":%llu,\"connections\":%llu,"
            "\"uptime_seconds\":%ld,\"workspaces\":%zu}}",
            (unsigned long long)app->metrics_requests,
            (unsigned long long)app->metrics_errors,
            (unsigned long long)app->metrics_connections,
            (long)(time(NULL) - app->start_time),
            app->workspaces.len);
        goto done;
    }


    /* --- clear-history --- */
    if (strcmp(cmd, "clear-history") == 0 || strcmp(cmd, "clear_history") == 0) {
        lmux_workspace *ws = lmux_workspace_focused(app);
        if (ws) {
            lmux_pane *p = lmux_pane_focused(ws);
            if (p && p->pty_fd >= 0) {
                const char ff = '\f';
                ssize_t unused_write = write(p->pty_fd, &ff, 1);
                (void)unused_write;
            }
        }
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- respawn-pane --- */
    if (strcmp(cmd, "respawn-pane") == 0 || strcmp(cmd, "respawn_pane") == 0) {
        char p_id[64] = {0};
        json_extract_string(args_json, "id", p_id, sizeof p_id);
        lmux_pane *p = NULL;
        if (p_id[0]) {
            for (size_t w = 0; w < app->workspaces.len && !p; w++) {
                lmux_workspace *ws = app->workspaces.items[w];
                for (size_t s = 0; s < ws->surfaces.len && !p; s++) {
                    lmux_surface *sf = ws->surfaces.items[s];
                    for (size_t pn = 0; pn < sf->panes.len; pn++) {
                        lmux_pane *cp = sf->panes.items[pn];
                        if (cp->id == (lmux_id)atol(p_id)) { p = cp; break; }
                    }
                }
            }
        } else {
            p = lmux_pane_focused(lmux_workspace_focused(app));
        }
        if (p) {
            pane_kill_pty(p);
        }
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- reorder-surface --- */
    if (strcmp(cmd, "reorder-surface") == 0 || strcmp(cmd, "reorder_surface") == 0) {
        char s_id[64] = {0}, idx_str[16] = {0};
        json_extract_string(args_json, "id", s_id, sizeof s_id);
        json_extract_string(args_json, "index", idx_str, sizeof idx_str);
        if (s_id[0] && idx_str[0]) {
            size_t new_idx = (size_t)atol(idx_str);
            lmux_workspace *ws = lmux_workspace_focused(app);
            if (!ws && app->workspaces.len > 0) ws = app->workspaces.items[0];
            if (ws) {
                for (size_t i = 0; i < ws->surfaces.len; i++) {
                    lmux_surface *s = ws->surfaces.items[i];
                    if (s->id == (lmux_id)atol(s_id)) {
                        if (new_idx >= ws->surfaces.len) new_idx = ws->surfaces.len - 1;
                        if (i != new_idx) {
                            void *tmp = ws->surfaces.items[i];
                            if (i < new_idx) {
                                memmove(&ws->surfaces.items[i], &ws->surfaces.items[i + 1],
                                        (new_idx - i) * sizeof(void *));
                            } else {
                                memmove(&ws->surfaces.items[new_idx + 1], &ws->surfaces.items[new_idx],
                                        (i - new_idx) * sizeof(void *));
                            }
                            ws->surfaces.items[new_idx] = tmp;
                        }
                        break;
                    }
                }
            }
        }
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- swap-pane --- */
    if (strcmp(cmd, "swap-pane") == 0 || strcmp(cmd, "swap_pane") == 0) {
        char id1[64] = {0}, id2[64] = {0};
        json_extract_string(args_json, "id1", id1, sizeof id1);
        json_extract_string(args_json, "id2", id2, sizeof id2);
        if (id1[0] && id2[0]) {
            lmux_pane *p1 = NULL, *p2 = NULL;
            lmux_surface *s1 = NULL, *s2 = NULL;
            size_t i1 = 0, i2 = 0;
            for (size_t w = 0; w < app->workspaces.len; w++) {
                lmux_workspace *ws = app->workspaces.items[w];
                for (size_t s = 0; s < ws->surfaces.len; s++) {
                    lmux_surface *surf = ws->surfaces.items[s];
                    for (size_t p = 0; p < surf->panes.len; p++) {
                        lmux_pane *cp = surf->panes.items[p];
                        if (cp->id == (lmux_id)atol(id1)) { p1 = cp; s1 = surf; i1 = p; }
                        if (cp->id == (lmux_id)atol(id2)) { p2 = cp; s2 = surf; i2 = p; }
                    }
                }
            }
            if (p1 && p2 && s1 && s2) {
                vec_remove(&s1->panes, i1);
                if (s1 == s2) {
                    if (i1 < i2) i2--;
                }
                vec_remove(&s2->panes, i2);
                /* Insert p2 at i1 in s1 */
                if (i1 > s1->panes.len) i1 = s1->panes.len;
                if (s1->panes.len == s1->panes.cap) {
                    s1->panes.cap = s1->panes.cap ? s1->panes.cap * 2 : VEC_GROW;
                    s1->panes.items = realloc(s1->panes.items, s1->panes.cap * sizeof(void *));
                }
                memmove(&s1->panes.items[i1+1], &s1->panes.items[i1],
                        (s1->panes.len - i1) * sizeof(void *));
                s1->panes.items[i1] = p2;
                s1->panes.len++;
                /* Insert p1 at i2 in s2 */
                if (i2 > s2->panes.len) i2 = s2->panes.len;
                if (s2->panes.len == s2->panes.cap) {
                    s2->panes.cap = s2->panes.cap ? s2->panes.cap * 2 : VEC_GROW;
                    s2->panes.items = realloc(s2->panes.items, s2->panes.cap * sizeof(void *));
                }
                memmove(&s2->panes.items[i2+1], &s2->panes.items[i2],
                        (s2->panes.len - i2) * sizeof(void *));
                s2->panes.items[i2] = p1;
                s2->panes.len++;
                lmux_event_push(app, "pane.swapped", "pane", "\"id1\":%u,\"id2\":%u", p1->id, p2->id);
            }
        }
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- break-pane --- */
    if (strcmp(cmd, "break-pane") == 0 || strcmp(cmd, "break_pane") == 0) {
        char p_id[64] = {0};
        json_extract_string(args_json, "id", p_id, sizeof p_id);
        lmux_pane *p = NULL;
        lmux_surface *src_surf = NULL;
        size_t src_idx = 0;
        for (size_t w = 0; w < app->workspaces.len && !src_surf; w++) {
            lmux_workspace *ws = app->workspaces.items[w];
            for (size_t s = 0; s < ws->surfaces.len && !src_surf; s++) {
                lmux_surface *surf = ws->surfaces.items[s];
                for (size_t pn = 0; pn < surf->panes.len; pn++) {
                    lmux_pane *cp = surf->panes.items[pn];
                    if ((!p_id[0] && cp->focused) || (p_id[0] && cp->id == (lmux_id)atol(p_id))) {
                        if (!p) { p = cp; src_surf = surf; src_idx = pn; }
                        break;
                    }
                }
            }
        }
        if (p && src_surf) {
            vec_remove(&src_surf->panes, src_idx);
            lmux_workspace *nws = lmux_workspace_create(app, NULL);
            lmux_surface *ns = lmux_surface_create(nws, NULL);
            vec_push(&ns->panes, p);
            ns->focused = true;
            p->focused = true;
            lmux_event_push(app, "pane.broken", "pane", "\"id\":%u,\"new_workspace_id\":%u",
                p->id, nws->id);
        }
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- join-pane --- */
    if (strcmp(cmd, "join-pane") == 0 || strcmp(cmd, "join_pane") == 0) {
        char src_id[64] = {0}, dst_id[64] = {0};
        json_extract_string(args_json, "source_id", src_id, sizeof src_id);
        json_extract_string(args_json, "destination_id", dst_id, sizeof dst_id);
        if (src_id[0] && dst_id[0]) {
            lmux_pane *src = NULL, *dst = NULL;
            lmux_surface *src_surf = NULL, *dst_surf = NULL;
            size_t src_idx = 0;
            for (size_t w = 0; w < app->workspaces.len; w++) {
                lmux_workspace *ws = app->workspaces.items[w];
                for (size_t s = 0; s < ws->surfaces.len; s++) {
                    lmux_surface *surf = ws->surfaces.items[s];
                    for (size_t p = 0; p < surf->panes.len; p++) {
                        lmux_pane *cp = surf->panes.items[p];
                        if (cp->id == (lmux_id)atol(src_id)) { src = cp; src_surf = surf; src_idx = p; }
                        if (cp->id == (lmux_id)atol(dst_id)) { dst = cp; dst_surf = surf; }
                    }
                }
            }
            if (src && dst && src_surf && dst_surf) {
                vec_remove(&src_surf->panes, src_idx);
                vec_push(&dst_surf->panes, src);
                lmux_event_push(app, "pane.joined", "pane", "\"src_id\":%u,\"dst_id\":%u",
                    src->id, dst->id);
            }
        }
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }
    /* --- workspace.env --- */
    if (strcmp(cmd, "workspace.env") == 0 || strcmp(cmd, "workspace_env") == 0) {
        char ws_id_str[64] = {0}, key[256] = {0}, value[4096] = {0};
        json_extract_string(args_json, "workspace_id", ws_id_str, sizeof ws_id_str);
        json_extract_string(args_json, "key", key, sizeof key);
        json_extract_string(args_json, "value", value, sizeof value);

        lmux_workspace *ws = lmux_workspace_focused(app);
        if (ws_id_str[0]) ws = lmux_workspace_by_id(app, (lmux_id)atol(ws_id_str));

        if (!ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"workspace not found\"}}");
            goto done;
        }

        if (key[0] && value[0]) {
            /* Set: find existing or add */
            char entry[4352];
            snprintf(entry, sizeof entry, "%s=%s", key, value);
            int found = 0;
            for (size_t ei = 0; ei < ws->env_vars.len; ei++) {
                char *e = ws->env_vars.items[ei];
                size_t klen = strlen(key);
                if (strncmp(e, key, klen) == 0 && e[klen] == '=') {
                    free(e);
                    ws->env_vars.items[ei] = strdup(entry);
                    found = 1;
                    break;
                }
            }
            if (!found) vec_push(&ws->env_vars, strdup(entry));
            written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
            goto done;
        } else if (key[0]) {
            /* Get */
            char val[4096] = {0};
            for (size_t ei = 0; ei < ws->env_vars.len; ei++) {
                char *e = ws->env_vars.items[ei];
                size_t klen = strlen(key);
                if (strncmp(e, key, klen) == 0 && e[klen] == '=') {
                    snprintf(val, sizeof val, "%s", e + klen + 1);
                    break;
                }
            }
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"value\":\"%s\"}}", val);
            goto done;
        } else {
            /* List all */
            char buf[8192];
            int n = snprintf(buf, sizeof buf, "{\"ok\":true,\"result\":{\"env\":[");
            for (size_t ei = 0; ei < ws->env_vars.len; ei++) {
                if (ei > 0) n += snprintf(buf + n, sizeof buf - n, ",");
                char *eq = strchr(ws->env_vars.items[ei], '=');
                n += snprintf(buf + n, sizeof buf - n,
                    "{\"key\":\"%.*s\"}",
                    eq ? (int)(eq - (char*)ws->env_vars.items[ei]) : 0,
                    (char*)ws->env_vars.items[ei]);
            }
            n += snprintf(buf + n, sizeof buf - n, "]}}");
            snprintf(result, result_cap, "%s", buf);
            goto done;
        }
    }

    /* --- auth --- */
    if (strcmp(cmd, "auth") == 0 || strcmp(cmd, "auth_status") == 0) {
        /* Auth status stub */
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"authenticated\":false,\"method\":\"none\"}}");
        goto done;
    }
    if (strcmp(cmd, "auth.login") == 0 || strcmp(cmd, "auth_login") == 0) {
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"note\":\"auth.login acknowledged (UI-based login pending)\"}}");
        goto done;
    }
    if (strcmp(cmd, "auth.logout") == 0 || strcmp(cmd, "auth_logout") == 0) {
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"note\":\"auth.logout acknowledged (UI-based logout pending)\"}}");
        goto done;
    }

    /* --- events --- */
    if (strcmp(cmd, "events") == 0) {
        char name_filter[128] = {0}, cat_filter[128] = {0};
        char after_seq_str[32] = {0};
        json_extract_string(args_json, "name", name_filter, sizeof name_filter);
        json_extract_string(args_json, "category", cat_filter, sizeof cat_filter);
        json_extract_string(args_json, "after_seq", after_seq_str, sizeof after_seq_str);
        /* Build result with optional filters encoded */
        char result_buf[2048];
        int n = snprintf(result_buf, sizeof result_buf,
            "{\"ok\":true,\"result\":{\"stream\":\"events\"");
        if (name_filter[0])
            n += snprintf(result_buf + n, sizeof result_buf - n,
                ",\"name_filter\":\"%s\"", name_filter);
        if (cat_filter[0])
            n += snprintf(result_buf + n, sizeof result_buf - n,
                ",\"category_filter\":\"%s\"", cat_filter);
        if (after_seq_str[0])
            n += snprintf(result_buf + n, sizeof result_buf - n,
                ",\"after_seq\":%s", after_seq_str);
        n += snprintf(result_buf + n, sizeof result_buf - n, "}}");
        snprintf(result, result_cap, "%s", result_buf);
        goto done;
    }

    /* --- help / list commands --- */
    if (strcmp(cmd, "help") == 0) {
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"commands\":["
            "\"workspace.create\",\"workspace.list\",\"workspace.close\","
            "\"workspace.select\",\"workspace.current\",\"workspace.rename\","
            "\"workspace.reorder\",\"workspace.refresh\","
            "\"surface.create\",\"surface.list\",\"surface.close\",\"surface.split\","
            "\"surface.focus\",\"surface.send_text\",\"surface.send_key\","
            "\"pane.list\",\"pane.focus\",\"pane.resize\",\"pane.focus_dir\","
            "\"notification.create\",\"notification.list\","
            "\"notification.clear\",\"notification.mark_read\","
            "\"notification.ring.add\",\"notification.ring.clear\",\"notification.ring.list\","
            "\"notification.hooks.add\",\"notification.hooks.remove\",\"notification.hooks.list\","
            "\"snapshot.save\",\"snapshot.load\",\"session.save\",\"session.restore\","
            "\"config.get\",\"config.set\",\"workspace.group.create\","
            "\"workspace.group.list\",\"workspace.group.add\",\"workspace.group.remove\","
            "\"agent.spawn\",\"agent.list\",\"agent.stop\","
            "\"read-screen\",\"capture-pane\","
            "\"display-message\",\"events\",\"capabilities\","
            "\"last-pane\",\"last-window\",\"move-surface\",\"surface.rename\","
            "\"next-window\",\"previous-window\",\"ping\",\"help\","
            "\"reload-config\",\"workspace.env\","
            "\"pipe-pane\",\"tree\",\"ssh\",\"wait-for\","
            "\"auth\",\"auth.login\",\"auth.logout\","
            "\"clear-history\",\"respawn-pane\",\"reorder-surface\","
            "\"swap-pane\",\"break-pane\",\"join-pane\""
            "]}}");
        goto done;
    }

    /* --- session.save --- */
    if (strcmp(cmd, "session.save") == 0) {
        lmux_app_auto_save(app);
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"path\":\"%s\"}}", app->auto_save_path);
        goto done;
    }

    /* --- session.restore --- */
    if (strcmp(cmd, "session.restore") == 0) {
        lmux_app_auto_restore(app);
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"path\":\"%s\"}}", app->auto_save_path);
        goto done;
    }

    /* --- config.get --- */
    if (strcmp(cmd, "config.get") == 0) {
        char key[256] = {0};
        json_extract_string(args_json, "key", key, sizeof key);
        if (!app->config) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"no_config\",\"message\":\"config not loaded\"}}");
            goto done;
        }
        if (!key[0]) {
            /* Return full config as JSON */
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"font_family\":\"%s\",\"font_size\":%d,"
                "\"theme\":\"%s\",\"scrollback_lines\":%d,"
                "\"show_sidebar\":%s,\"show_notifications_panel\":%s,"
                "\"auto_save_session\":%s,\"auto_save_interval_sec\":%d,"
                "\"default_shell\":\"%s\","
                "\"agent_claude_code\":\"%s\",\"agent_opencode\":\"%s\","
                "\"agent_codex\":\"%s\",\"agent_aider\":\"%s\",\"agent_goose\":\"%s\"}}",
                app->config->font_family, app->config->font_size,
                app->config->theme, app->config->scrollback_lines,
                app->config->show_sidebar ? "true" : "false",
                app->config->show_notifications_panel ? "true" : "false",
                app->config->auto_save_session ? "true" : "false",
                app->config->auto_save_interval_sec,
                app->config->default_shell,
                app->config->agent_paths[0], app->config->agent_paths[1],
                app->config->agent_paths[2], app->config->agent_paths[3],
                app->config->agent_paths[4]);
        } else {
            /* Return single key value */
            char val[1024] = {0};
            if (strcmp(key, "font_family") == 0) snprintf(val, sizeof val, "\"%s\"", app->config->font_family);
            else if (strcmp(key, "font_size") == 0) snprintf(val, sizeof val, "%d", app->config->font_size);
            else if (strcmp(key, "theme") == 0) snprintf(val, sizeof val, "\"%s\"", app->config->theme);
            else if (strcmp(key, "scrollback_lines") == 0) snprintf(val, sizeof val, "%d", app->config->scrollback_lines);
            else if (strcmp(key, "show_sidebar") == 0) snprintf(val, sizeof val, "%s", app->config->show_sidebar ? "true" : "false");
            else if (strcmp(key, "auto_save_session") == 0) snprintf(val, sizeof val, "%s", app->config->auto_save_session ? "true" : "false");
            else if (strcmp(key, "auto_save_interval_sec") == 0) snprintf(val, sizeof val, "%d", app->config->auto_save_interval_sec);
            else if (strcmp(key, "default_shell") == 0) snprintf(val, sizeof val, "\"%s\"", app->config->default_shell);
            else snprintf(val, sizeof val, "null");
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"key\":\"%s\",\"value\":%s}}", key, val);
        }
        goto done;
    }

    /* --- config.set --- */
    if (strcmp(cmd, "config.set") == 0) {
        char key[256] = {0}, value[4096] = {0};
        json_extract_string(args_json, "key", key, sizeof key);
        json_extract_string(args_json, "value", value, sizeof value);
        if (!key[0] || !value[0] || !app->config) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing key and/or value\"}}");
            goto done;
        }
        if (strcmp(key, "font_family") == 0) snprintf(app->config->font_family, sizeof app->config->font_family, "%s", value);
        else if (strcmp(key, "font_size") == 0) app->config->font_size = (int)atol(value);
        else if (strcmp(key, "theme") == 0) snprintf(app->config->theme, sizeof app->config->theme, "%s", value);
        else if (strcmp(key, "scrollback_lines") == 0) app->config->scrollback_lines = (int)atol(value);
        else if (strcmp(key, "show_sidebar") == 0) app->config->show_sidebar = (strcmp(value, "true") == 0);
        else if (strcmp(key, "show_notifications_panel") == 0) app->config->show_notifications_panel = (strcmp(value, "true") == 0);
        else if (strcmp(key, "auto_save_session") == 0) app->config->auto_save_session = (strcmp(value, "true") == 0);
        else if (strcmp(key, "auto_save_interval_sec") == 0) app->config->auto_save_interval_sec = (int)atol(value);
        else if (strcmp(key, "default_shell") == 0) snprintf(app->config->default_shell, sizeof app->config->default_shell, "%s", value);
        else if (strcmp(key, "agent_claude_code") == 0) snprintf(app->config->agent_paths[0], 512, "%s", value);
        else if (strcmp(key, "agent_opencode") == 0) snprintf(app->config->agent_paths[1], 512, "%s", value);
        else if (strcmp(key, "agent_codex") == 0) snprintf(app->config->agent_paths[2], 512, "%s", value);
        else if (strcmp(key, "agent_aider") == 0) snprintf(app->config->agent_paths[3], 512, "%s", value);
        else if (strcmp(key, "agent_goose") == 0) snprintf(app->config->agent_paths[4], 512, "%s", value);
        /* Save config to disk */
        char cpath[512];
        lmux_config_path(cpath, sizeof cpath);
        lmux_config_save(app->config, cpath);
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- workspace.group.create --- */
    if (strcmp(cmd, "workspace.group.create") == 0) {
        char name[128] = {0};
        json_extract_string(args_json, "name", name, sizeof name);
        if (!name[0] || !app->config) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing name\"}}");
            goto done;
        }
        lmux_workspace_group *g = lmux_workspace_group_create(app->config, name);
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"name\":\"%s\"}}", g ? g->name : name);
        goto done;
    }

    /* --- workspace.group.list --- */
    if (strcmp(cmd, "workspace.group.list") == 0) {
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{\"groups\":[");
        if (app->config) {
            for (size_t i = 0; i < app->config->workspace_groups.len; i++) {
                lmux_workspace_group *g = app->config->workspace_groups.items[i];
                RESULT_GROW;
                if (i > 0) written += snprintf(result + written, result_cap - written, ",");
                written += snprintf(result + written, result_cap - written,
                    "{\"name\":\"%s\",\"workspace_count\":%zu}", g->name, g->workspace_ids.len);
            }
        }
        written += snprintf(result + written, result_cap - written, "]}}");
        goto done;
    }

    /* --- workspace.group.add --- */
    if (strcmp(cmd, "workspace.group.add") == 0) {
        char name[128] = {0}, ws_id_str[64] = {0};
        json_extract_string(args_json, "name", name, sizeof name);
        json_extract_string(args_json, "workspace_id", ws_id_str, sizeof ws_id_str);
        if (!name[0] || !ws_id_str[0] || !app->config) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing name and/or workspace_id\"}}");
            goto done;
        }
        lmux_id wid = (lmux_id)atol(ws_id_str);
        bool ok = lmux_workspace_group_add(app->config, name, wid);
        written = snprintf(result, result_cap,
            "{\"ok\":%s,\"result\":{}}", ok ? "true" : "false");
        goto done;
    }

    /* --- workspace.group.remove --- */
    if (strcmp(cmd, "workspace.group.remove") == 0) {
        char name[128] = {0}, ws_id_str[64] = {0};
        json_extract_string(args_json, "name", name, sizeof name);
        json_extract_string(args_json, "workspace_id", ws_id_str, sizeof ws_id_str);
        if (!name[0] || !ws_id_str[0] || !app->config) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing name and/or workspace_id\"}}");
            goto done;
        }
        lmux_id wid = (lmux_id)atol(ws_id_str);
        bool ok = lmux_workspace_group_remove(app->config, name, wid);
        written = snprintf(result, result_cap,
            "{\"ok\":%s,\"result\":{}}", ok ? "true" : "false");
        goto done;
    }

    /* --- agent.spawn --- */
    if (strcmp(cmd, "agent.spawn") == 0) {
        char name[64] = {0}, command[1024] = {0}, ws_id_str[64] = {0};
        json_extract_string(args_json, "name", name, sizeof name);
        json_extract_string(args_json, "command", command, sizeof command);
        json_extract_string(args_json, "workspace_id", ws_id_str, sizeof ws_id_str);
        if (!name[0] || !command[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing name and/or command\"}}");
            goto done;
        }
        lmux_workspace *ws = ws_id_str[0] ? lmux_workspace_by_id(app, (lmux_id)atol(ws_id_str))
                                          : lmux_workspace_focused(app);
        if (!ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"workspace not found\"}}");
            goto done;
        }
        /* Find or create a surface for the agent */
        lmux_surface *s = lmux_surface_focused(ws);
        if (!s && ws->surfaces.len > 0) s = ws->surfaces.items[0];
        if (!s) s = lmux_surface_create(ws, name);
        if (!s) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"create_failed\",\"message\":\"failed to create surface\"}}");
            goto done;
        }
        /* Split a new pane with the agent command */
        lmux_pane *p = lmux_pane_split(ws, s, LMUX_SPLIT_HORIZONTAL, command, true);
        if (!p) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"create_failed\",\"message\":\"failed to create pane\"}}");
            goto done;
        }
        /* Create agent tracking entry */
        lmux_agent *ag = calloc(1, sizeof *ag);
        ag->id = app->next_agent_id++;
        snprintf(ag->name, sizeof ag->name, "%s", name);
        snprintf(ag->command, sizeof ag->command, "%s", command);
        ag->pid = p->child_pid;
        ag->status = p->child_pid > 0 ? 1 : 0;
        ag->workspace_id = ws->id;
        ag->pane_id = p->id;
        ag->started_at = time(NULL);
        vec_push(&app->agents, ag);
        lmux_event_push(app, "agent.spawned", "agent", "\"id\":%u,\"name\":\"%s\",\"pane_id\":%u", ag->id, name, p->id);
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"id\":%u,\"name\":\"%s\",\"pane_id\":%u,\"pid\":%d}}",
            ag->id, name, p->id, (int)ag->pid);
        goto done;
    }

    /* --- agent.list --- */
    if (strcmp(cmd, "agent.list") == 0) {
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{\"agents\":[");
        for (size_t i = 0; i < app->agents.len; i++) {
            lmux_agent *ag = app->agents.items[i];
            RESULT_GROW;
            char _ne[256];
            if (i > 0) written += snprintf(result + written, result_cap - written, ",");
            written += snprintf(result + written, result_cap - written,
                "{\"id\":%u,\"name\":\"%s\",\"status\":%d,\"workspace_id\":%u,\"pane_id\":%u,\"pid\":%d,\"started_at\":%lld}",
                ag->id, json_escape_str(_ne, sizeof _ne, ag->name), ag->status, ag->workspace_id, ag->pane_id, (int)ag->pid, (long long)ag->started_at);
        }
        written += snprintf(result + written, result_cap - written, "]}}");
        goto done;
    }

    /* --- agent.stop --- */
    if (strcmp(cmd, "agent.stop") == 0) {
        char id_str[64] = {0};
        json_extract_string(args_json, "id", id_str, sizeof id_str);
        if (!id_str[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing id\"}}");
            goto done;
        }
        lmux_id aid = (lmux_id)atol(id_str);
        lmux_agent *found = NULL;
        for (size_t i = 0; i < app->agents.len; i++) {
            lmux_agent *ag = app->agents.items[i];
            if (ag->id == aid) { found = ag; break; }
        }
        if (!found) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"agent not found\"}}");
            goto done;
        }
        if (found->pid > 0) {
            kill(found->pid, SIGTERM);
            found->status = 0;
            found->pid = 0;
        }
        lmux_event_push(app, "agent.stopped", "agent", "\"id\":%u", found->id);
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- agent.hibernate --- */
    if (strcmp(cmd, "agent.hibernate") == 0) {
        char id_str[64] = {0};
        json_extract_string(args_json, "id", id_str, sizeof id_str);
        if (!id_str[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing id\"}}");
            goto done;
        }
        lmux_id aid = (lmux_id)atol(id_str);
        if (lmux_agent_hibernate(app, aid)) {
            written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"hibernate_failed\",\"message\":\"could not hibernate agent\"}}");
        }
        goto done;
    }

    /* --- agent.resume --- */
    if (strcmp(cmd, "agent.resume") == 0) {
        char id_str[64] = {0};
        json_extract_string(args_json, "id", id_str, sizeof id_str);
        if (!id_str[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing id\"}}");
            goto done;
        }
        lmux_id aid = (lmux_id)atol(id_str);
        if (lmux_agent_resume(app, aid)) {
            written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"resume_failed\",\"message\":\"could not resume agent\"}}");
        }
        goto done;
    }

    /* --- agent.hibernation.status --- */
    if (strcmp(cmd, "agent.hibernation.status") == 0) {
        size_t hibernated = 0, active = 0;
        for (size_t i = 0; i < app->agents.len; i++) {
            lmux_agent *a = app->agents.items[i];
            if (a->hibernated) hibernated++;
            else if (a->pid > 0) active++;
        }
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"hibernated\":%zu,\"active\":%zu,\"total\":%zu}}",
            hibernated, active, app->agents.len);
        goto done;
    }

    /* --- pipe-pane (real implementation) --- */
    if (strcmp(cmd, "pipe-pane") == 0 || strcmp(cmd, "pipe_pane") == 0) {
        char p_id[64] = {0}, command[1024] = {0};
        json_extract_string(args_json, "id", p_id, sizeof p_id);
        json_extract_string(args_json, "command", command, sizeof command);
        lmux_pane *p = NULL;
        if (p_id[0]) {
            for (size_t w = 0; w < app->workspaces.len && !p; w++) {
                lmux_workspace *ws = app->workspaces.items[w];
                for (size_t s = 0; s < ws->surfaces.len && !p; s++) {
                    lmux_surface *surf = ws->surfaces.items[s];
                    for (size_t pn = 0; pn < surf->panes.len; pn++) {
                        if (((lmux_pane *)surf->panes.items[pn])->id == (lmux_id)atol(p_id)) { p = (lmux_pane *)surf->panes.items[pn]; break; }
                    }
                }
            }
        } else {
            lmux_workspace *ws = lmux_workspace_focused(app);
            if (ws) p = lmux_pane_focused(ws);
        }
        if (!p) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"pane not found\"}}");
            goto done;
        }
        if (p->pty_fd < 0 || !command[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"pane has no pty or missing command\"}}");
            goto done;
        }
        /* Fork a child that reads from the pty and pipes to the command */
        pid_t pipe_pid = fork();
        if (pipe_pid == 0) {
            /* Child: redirect stdin from pty master fd, exec the command */
            dup2(p->pty_fd, STDIN_FILENO);
            close(p->pty_fd);
            execlp("/bin/sh", "/bin/sh", "-c", command, (char *)NULL);
            _exit(127);
        }
        lmux_event_push(app, "pane.piped", "pane", "\"id\":%u,\"pipe_pid\":%d", p->id, (int)pipe_pid);
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"pid\":%d}}", (int)pipe_pid);
        goto done;
    }

    /* --- wait-for (real implementation) --- */
    if (strcmp(cmd, "wait-for") == 0 || strcmp(cmd, "wait_for") == 0) {
        char name[256] = {0}, action[64] = {0};
        json_extract_string(args_json, "name", name, sizeof name);
        json_extract_string(args_json, "action", action, sizeof action);
        if (!name[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing name\"}}");
            goto done;
        }
        /* Find or create the wait point */
        lmux_wait_point *wp = NULL;
        for (size_t i = 0; i < app->wait_points.len; i++) {
            lmux_wait_point *w = app->wait_points.items[i];
            if (strcmp(w->name, name) == 0) { wp = w; break; }
        }
        if (!wp) {
            wp = calloc(1, sizeof *wp);
            snprintf(wp->name, sizeof wp->name, "%s", name);
            wp->signaled = false;
            vec_push(&app->wait_points, wp);
        }
        if (action[0] && strcmp(action, "signal") == 0) {
            wp->signaled = true;
            lmux_event_push(app, "wait.signaled", "sync", "\"name\":\"%s\"", name);
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"name\":\"%s\",\"signaled\":true}}", name);
        } else if (action[0] && strcmp(action, "reset") == 0) {
            wp->signaled = false;
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"name\":\"%s\",\"signaled\":false}}", name);
        } else {
            /* Check/wait: return current state (non-blocking) */
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"name\":\"%s\",\"signaled\":%s}}",
                name, wp->signaled ? "true" : "false");
        }
        goto done;
    }

    /* --- reload-config (real implementation) --- */
    if (strcmp(cmd, "reload-config") == 0 || strcmp(cmd, "reload_config") == 0) {
        bool ok = false;
        if (app->config) {
            char cpath[512];
            lmux_config_path(cpath, sizeof cpath);
            ok = lmux_config_load(app->config, cpath);
        }
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"reloaded\":%s}}", ok ? "true" : "false");
        goto done;
    }

    /* --- capabilities (updated) --- */
    if (strcmp(cmd, "capabilities") == 0) {
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"capabilities\":["
            "\"workspace.create\",\"workspace.list\",\"workspace.close\","
            "\"workspace.select\",\"workspace.current\",\"workspace.rename\","
            "\"workspace.reorder\",\"workspace.refresh\","
            "\"surface.create\",\"surface.list\",\"surface.close\",\"surface.split\","
            "\"surface.focus\",\"surface.send_text\",\"surface.send_key\","
            "\"pane.list\",\"pane.focus\",\"pane.resize\",\"pane.focus_dir\","
            "\"notification.create\",\"notification.list\","
            "\"notification.clear\",\"notification.mark_read\","
            "\"notification.ring.add\",\"notification.ring.clear\",\"notification.ring.list\","
            "\"notification.hooks.add\",\"notification.hooks.remove\",\"notification.hooks.list\","
            "\"snapshot.save\",\"snapshot.load\",\"session.save\",\"session.restore\","
            "\"config.get\",\"config.set\","
            "\"workspace.group.create\",\"workspace.group.list\","
            "\"workspace.group.add\",\"workspace.group.remove\","
            "\"agent.spawn\",\"agent.list\",\"agent.stop\","
            "\"agent.hibernate\",\"agent.resume\",\"agent.hibernation.status\","
            "\"read-screen\",\"capture-pane\","
            "\"display-message\",\"events\",\"capabilities\","
            "\"last-pane\",\"last-window\",\"move-surface\",\"surface.rename\","
            "\"next-window\",\"previous-window\",\"ping\",\"help\","
            "\"reload-config\",\"workspace.env\","
            "\"pipe-pane\",\"tree\",\"ssh\",\"wait-for\","
            "\"auth\",\"auth.login\",\"auth.logout\","
            "\"clear-history\",\"respawn-pane\",\"reorder-surface\","
            "\"swap-pane\",\"break-pane\",\"join-pane\","
            "\"health.live\",\"health.ready\","
            "\"metrics\","
            "\"browser.open\",\"browser.snapshot\",\"browser.click\","
            "\"browser.fill\",\"browser.evaluate\",\"browser.screenshot\","
            "\"browser.list\",\"browser.import\","
            "\"window.create\",\"window.list\",\"window.close\","
            "\"window.focus\",\"window.move-workspace\","
            "\"ssh.session.create\",\"ssh.session.list\",\"ssh.session.kill\","
            "\"ssh.session.save\",\"ssh.session.restore\","
            "\"feed.panel.create\",\"feed.panel.list\",\"feed.panel.close\","
            "\"feed.entry.add\",\"feed.entry.list\",\"feed.panel.clear\","
            "\"search.start\",\"search.next\",\"search.prev\","
            "\"search.cancel\",\"search.status\","
            "\"calendar.import\",\"calendar.today\",\"calendar.upcoming\","
            "\"email.import\",\"email.list\",\"email.search\","
            "\"weather.get\",\"weather.set_location\",\"weather.refresh\","
            "\"clipboard.copy\",\"clipboard.paste\",\"clipboard.list\",\"clipboard.clear\","
            "\"template.list\",\"template.create\",\"template.save\",\"template.delete\","
            "\"profile.status\",\"profile.start\",\"profile.stop\",\"profile.list\","
            "\"cloud.vms.list\",\"cloud.vms.create\",\"cloud.vms.destroy\",\"cloud.vms.ssh\","
            "\"companion.register\",\"companion.status\",\"companion.notify\",\"companion.unregister\","
            "\"team.create\",\"team.add\",\"team.remove\",\"team.list\",\"team.dispatch\",\"team.delete\""
            "]}}");
        goto done;
    }

    /* --- browser commands --- */
    if (strcmp(cmd, "browser.open") == 0) {
        char url[2048] = {0};
        json_extract_string(args_json, "url", url, sizeof url);
        if (!url[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_args\",\"message\":\"url required\"}}");
            goto done;
        }
        /* Browser is managed by GUI — daemon just acknowledges */
        lmux_event_push(app, "browser.opened", "browser", "\"url\":\"%s\"", url);
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{\"url\":\"%s\"}}", url);
        goto done;
    }

    if (strcmp(cmd, "browser.snapshot") == 0) {
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"status\":\"snapshot_not_implemented\"}}");
        goto done;
    }

    if (strcmp(cmd, "browser.click") == 0) {
        char selector[512] = {0};
        json_extract_string(args_json, "selector", selector, sizeof selector);
        if (!selector[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_args\",\"message\":\"selector required\"}}");
            goto done;
        }
        lmux_event_push(app, "browser.clicked", "browser", "\"selector\":\"%s\"", selector);
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    if (strcmp(cmd, "browser.fill") == 0) {
        char selector[512] = {0}, value[2048] = {0};
        json_extract_string(args_json, "selector", selector, sizeof selector);
        json_extract_string(args_json, "value", value, sizeof value);
        if (!selector[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_args\",\"message\":\"selector required\"}}");
            goto done;
        }
        lmux_event_push(app, "browser.filled", "browser",
            "\"selector\":\"%s\",\"value\":\"%s\"", selector, value);
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    if (strcmp(cmd, "browser.evaluate") == 0) {
        char js[4096] = {0};
        json_extract_string(args_json, "js", js, sizeof js);
        if (!js[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_args\",\"message\":\"js required\"}}");
            goto done;
        }
        /* Acknowledge — actual execution happens in GUI */
        lmux_event_push(app, "browser.evaluated", "browser", "\"js_length\":%zu", strlen(js));
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"status\":\"eval_queued\"}}");
        goto done;
    }

    if (strcmp(cmd, "browser.screenshot") == 0) {
        char path[1024] = {0};
        json_extract_string(args_json, "path", path, sizeof path);
        if (!path[0]) strncpy(path, "/tmp/lmux-screenshot.png", sizeof path - 1);
        lmux_event_push(app, "browser.screenshot", "browser", "\"path\":\"%s\"", path);
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"path\":\"%s\"}}", path);
        goto done;
    }

    if (strcmp(cmd, "browser.list") == 0) {
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"browsers\":[]}}");
        goto done;
    }

    /* --- calendar.import --- */
    if (strcmp(cmd, "calendar.import") == 0) {
        char path[1024] = {0};
        json_extract_string(args_json, "path", path, sizeof path);
        if (!path[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing path\"}}");
            goto done;
        }

        FILE *fp = fopen(path, "r");
        if (!fp) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"file_error\",\"message\":\"cannot open file\"}}");
            goto done;
        }
        fseek(fp, 0, SEEK_END);
        long fsize = ftell(fp);
        fseek(fp, 0, SEEK_SET);
        if (fsize <= 0 || fsize > 2 * 1024 * 1024) {
            fclose(fp);
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"file_error\",\"message\":\"file too large or empty\"}}");
            goto done;
        }
        char *fbuf = malloc(fsize + 1);
        if (!fbuf) { fclose(fp); goto done; }
        fread(fbuf, 1, fsize, fp);
        fbuf[fsize] = '\0';
        fclose(fp);

        /* Create feed panel for calendar */
        lmux_feed_panel *panel = lmux_feed_panel_create(app, "Calendar", "event");
        if (!panel) { free(fbuf); goto done; }

        int count = 0;
        /* Parse VEVENT blocks */
        const char *ev = fbuf;
        while ((ev = strstr(ev, "BEGIN:VEVENT")) != NULL) {
            ev += 12; /* skip "BEGIN:VEVENT" */
            const char *ev_end = strstr(ev, "END:VEVENT");
            if (!ev_end) break;

            char summary[256] = {0}, dtstart[64] = {0}, dtend[64] = {0};
            char description[1024] = {0};

            /* Extract SUMMARY */
            const char *s = strstr(ev, "SUMMARY:");
            if (s && s < ev_end) {
                s += 8;
                while (*s == ' ') s++;
                const char *se = strchr(s, '\n');
                if (!se || se > ev_end) se = ev_end;
                size_t len = (size_t)(se - s);
                if (len >= sizeof summary) len = sizeof summary - 1;
                snprintf(summary, len + 1, "%s", s);
            }

            /* Extract DTSTART */
            s = strstr(ev, "DTSTART:");
            if (s && s < ev_end) {
                s += 8;
                while (*s == ' ') s++;
                const char *se = strchr(s, '\n');
                if (!se || se > ev_end) se = ev_end;
                size_t len = (size_t)(se - s);
                if (len >= sizeof dtstart) len = sizeof dtstart - 1;
                snprintf(dtstart, len + 1, "%s", s);
            }

            /* Extract DTEND */
            s = strstr(ev, "DTEND:");
            if (s && s < ev_end) {
                s += 6;
                while (*s == ' ') s++;
                const char *se = strchr(s, '\n');
                if (!se || se > ev_end) se = ev_end;
                size_t len = (size_t)(se - s);
                if (len >= sizeof dtend) len = sizeof dtend - 1;
                snprintf(dtend, len + 1, "%s", s);
            }

            /* Extract DESCRIPTION */
            s = strstr(ev, "DESCRIPTION:");
            if (s && s < ev_end) {
                s += 12;
                while (*s == ' ') s++;
                const char *se = strchr(s, '\n');
                if (!se || se > ev_end) se = ev_end;
                size_t len = (size_t)(se - s);
                if (len >= sizeof description) len = sizeof description - 1;
                snprintf(description, len + 1, "%s", s);
            }

            /* Format as feed entry text: [date] summary (time) */
            char text[1600];
            if (dtstart[0]) {
                /* Parse YYYYMMDDTHHMMSS format for display */
                int year = 0, month = 0, day = 0, hour = 0, min = 0;
                if (sscanf(dtstart, "%4d%2d%2dT%2d%2d", &year, &month, &day, &hour, &min) >= 3) {
                    snprintf(text, sizeof text, "[%04d-%02d-%02d %02d:%02d] %s",
                        year, month, day, hour, min, summary[0] ? summary : "Untitled");
                } else {
                    snprintf(text, sizeof text, "[%s] %s", dtstart, summary[0] ? summary : "Untitled");
                }
            } else {
                snprintf(text, sizeof text, "%s", summary[0] ? summary : "Untitled");
            }

            lmux_feed_entry_add(app, panel->id, "event", "calendar", text);
            count++;
            ev = ev_end + 10; /* skip "END:VEVENT" */
        }

        free(fbuf);
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"panel_id\":%u,\"count\":%d}}",
            panel->id, count);
        goto done;
    }

    /* --- calendar.today --- */
    if (strcmp(cmd, "calendar.today") == 0) {
        /* Return events from all calendar panels that match today's date */
        char today_str[16] = {0};
        struct tm *tm = gmtime(&(time_t){time(NULL)});
        snprintf(today_str, sizeof today_str, "%04d-%02d-%02d",
            tm->tm_year + 1900, tm->tm_mon + 1, tm->tm_mday);

        char buf[8192];
        int n = snprintf(buf, sizeof buf,
            "{\"ok\":true,\"result\":{\"date\":\"%s\",\"events\":[", today_str);
        int count = 0;

        for (size_t pi = 0; pi < app->feed_panels.len; pi++) {
            lmux_feed_panel *panel = app->feed_panels.items[pi];
            /* Only calendar panels */
            if (strcmp(panel->filter, "event") != 0) continue;

            for (size_t ei = 0; ei < app->feed_entries.len; ei++) {
                lmux_feed_entry *entry = app->feed_entries.items[ei];
                if (entry->panel_id != panel->id) continue;
                if (strstr(entry->text, today_str)) {
                    if (count > 0) n += snprintf(buf + n, sizeof buf - n, ",");
                    n += snprintf(buf + n, sizeof buf - n,
                        "{\"seq\":%lu,\"text\":\"%s\"}", (unsigned long)entry->seq, JESC(entry->text));
                    count++;
                }
            }
        }
        n += snprintf(buf + n, sizeof buf - n, "],\"count\":%d}}", count);
        written = snprintf(result, result_cap, "%s", buf);
        goto done;
    }

    /* --- calendar.upcoming --- */
    if (strcmp(cmd, "calendar.upcoming") == 0) {
        int days = 7;
        /* Extract days parameter */
        const char *dp = strstr(args_json, "\"days\"");
        if (dp) {
            dp += 6;
            while (*dp == ' ' || *dp == ':') dp++;
            days = atoi(dp);
            if (days <= 0) days = 7;
            if (days > 365) days = 365;
        }

        char buf[16384];
        int n = snprintf(buf, sizeof buf,
            "{\"ok\":true,\"result\":{\"days\":%d,\"events\":[", days);
        int count = 0;

        for (size_t pi = 0; pi < app->feed_panels.len; pi++) {
            lmux_feed_panel *panel = app->feed_panels.items[pi];
            if (strcmp(panel->filter, "event") != 0) continue;

            for (size_t ei = 0; ei < app->feed_entries.len; ei++) {
                lmux_feed_entry *entry = app->feed_entries.items[ei];
                if (entry->panel_id != panel->id) continue;
                if (count > 0) n += snprintf(buf + n, sizeof buf - n, ",");
                n += snprintf(buf + n, sizeof buf - n,
                    "{\"seq\":%lu,\"text\":\"%s\"}", (unsigned long)entry->seq, JESC(entry->text));
                count++;
                if (count >= 100) break; /* safety limit */
            }
        }
        n += snprintf(buf + n, sizeof buf - n, "],\"count\":%d}}", count);
        written = snprintf(result, result_cap, "%s", buf);
        goto done;
    }

    /* --- email.import --- */
    if (strcmp(cmd, "email.import") == 0) {
        char path[1024] = {0};
        json_extract_string(args_json, "path", path, sizeof path);
        if (!path[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing path\"}}");
            goto done;
        }

        FILE *fp = fopen(path, "r");
        if (!fp) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"file_error\",\"message\":\"cannot open file\"}}");
            goto done;
        }
        fseek(fp, 0, SEEK_END);
        long fsize = ftell(fp);
        fseek(fp, 0, SEEK_SET);
        if (fsize < 0 || fsize > 2 * 1024 * 1024) {
            fclose(fp);
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"file_error\",\"message\":\"file too large or unreadable\"}}");
            goto done;
        }

        /* Create feed panel for email */
        lmux_feed_panel *panel = lmux_feed_panel_create(app, "Email", "message");
        if (!panel) { fclose(fp); goto done; }

        int count = 0;
        if (fsize == 0) {
            fclose(fp);
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"panel_id\":%u,\"count\":0}}",
                panel->id);
            goto done;
        }

        char *fbuf = malloc(fsize + 1);
        if (!fbuf) { fclose(fp); goto done; }
        (void)fread(fbuf, 1, fsize, fp);
        fbuf[fsize] = '\0';
        fclose(fp);

        /* Parse mbox: "From " line starts a message */
        char *line = fbuf;
        char from[256] = {0}, subject[256] = {0}, date[128] = {0};
        bool in_headers = true;

        while (line && *line) {
            if (strncmp(line, "From ", 5) == 0) {
                /* Save previous email if we had one */
                if (from[0] || subject[0]) {
                    char text[1600];
                    snprintf(text, sizeof text, "[%s] From: %s — %s",
                        date[0] ? date : "unknown",
                        from[0] ? from : "unknown",
                        subject[0] ? subject : "(no subject)");
                    lmux_feed_entry_add(app, panel->id, "message", "email", text);
                    count++;
                    from[0] = '\0'; subject[0] = '\0'; date[0] = '\0';
                }
                /* Parse From line */
                const char *sp = line + 5;
                while (*sp == ' ') sp++;
                const char *nl = strchr(sp, '\n');
                size_t len = nl ? (size_t)(nl - sp) : strlen(sp);
                if (len >= sizeof from) len = sizeof from - 1;
                snprintf(from, len + 1, "%s", sp);
                in_headers = true;
            } else if (in_headers) {
                if (strncmp(line, "Subject:", 8) == 0) {
                    const char *sp = line + 8;
                    while (*sp == ' ') sp++;
                    const char *nl = strchr(sp, '\n');
                    size_t len = nl ? (size_t)(nl - sp) : strlen(sp);
                    if (len >= sizeof subject) len = sizeof subject - 1;
                    snprintf(subject, len + 1, "%s", sp);
                } else if (strncmp(line, "Date:", 5) == 0) {
                    const char *sp = line + 5;
                    while (*sp == ' ') sp++;
                    const char *nl = strchr(sp, '\n');
                    size_t len = nl ? (size_t)(nl - sp) : strlen(sp);
                    if (len >= sizeof date) len = sizeof date - 1;
                    snprintf(date, len + 1, "%s", sp);
                } else if (*line == '\n') {
                    in_headers = false; /* end of headers */
                }
            }
            /* Advance to next line */
            const char *nl = strchr(line, '\n');
            line = nl ? (char *)nl + 1 : line + strlen(line);
        }

        /* Save last email */
        if (from[0] || subject[0]) {
            char text[1600];
            snprintf(text, sizeof text, "[%s] From: %s — %s",
                date[0] ? date : "unknown",
                from[0] ? from : "unknown",
                subject[0] ? subject : "(no subject)");
            lmux_feed_entry_add(app, panel->id, "message", "email", text);
            count++;
        }

        free(fbuf);
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"panel_id\":%u,\"count\":%d}}",
            panel->id, count);
        goto done;
    }

    /* --- email.list --- */
    if (strcmp(cmd, "email.list") == 0) {
        char buf[8192];
        int n = snprintf(buf, sizeof buf,
            "{\"ok\":true,\"result\":{\"emails\":[");

        int count = 0;
        for (size_t pi = 0; pi < app->feed_panels.len; pi++) {
            lmux_feed_panel *panel = app->feed_panels.items[pi];
            if (strcmp(panel->filter, "message") != 0) continue;

            for (size_t ei = 0; ei < app->feed_entries.len; ei++) {
                lmux_feed_entry *entry = app->feed_entries.items[ei];
                if (entry->panel_id != panel->id) continue;
                if (count > 0) n += snprintf(buf + n, sizeof buf - n, ",");
                n += snprintf(buf + n, sizeof buf - n,
                    "{\"seq\":%lu,\"text\":\"%s\"}",
                    (unsigned long)entry->seq, JESC(entry->text));
                count++;
            }
        }
        n += snprintf(buf + n, sizeof buf - n, "],\"count\":%d}}", count);
        written = snprintf(result, result_cap, "%s", buf);
        goto done;
    }

    /* --- email.search --- */
    if (strcmp(cmd, "email.search") == 0) {
        char query[256] = {0};
        json_extract_string(args_json, "query", query, sizeof query);
        if (!query[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing query\"}}");
            goto done;
        }

        char buf[8192];
        int n = snprintf(buf, sizeof buf,
            "{\"ok\":true,\"result\":{\"emails\":[");

        int count = 0;
        for (size_t pi = 0; pi < app->feed_panels.len; pi++) {
            lmux_feed_panel *panel = app->feed_panels.items[pi];
            if (strcmp(panel->filter, "message") != 0) continue;

            for (size_t ei = 0; ei < app->feed_entries.len; ei++) {
                lmux_feed_entry *entry = app->feed_entries.items[ei];
                if (entry->panel_id != panel->id) continue;
                if (strstr(entry->text, query)) {
                    if (count > 0) n += snprintf(buf + n, sizeof buf - n, ",");
                    n += snprintf(buf + n, sizeof buf - n,
                        "{\"seq\":%lu,\"text\":\"%s\"}",
                        (unsigned long)entry->seq, JESC(entry->text));
                    count++;
                }
            }
        }
        n += snprintf(buf + n, sizeof buf - n, "],\"count\":%d}}", count);
        written = snprintf(result, result_cap, "%s", buf);
        goto done;
    }

    /* --- weather.get --- */
    if (strcmp(cmd, "weather.get") == 0) {
        char location[256] = {0};
        json_extract_string(args_json, "location", location, sizeof location);
        if (!location[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing location\"}}");
            goto done;
        }
        /* Generate simulated weather data (no API call needed) */
        int temp_c = 15 + (hash_string(location) % 25); /* 15-39°C */
        const char *conditions[] = {"Sunny", "Cloudy", "Rainy", "Partly Cloudy", "Clear"};
        const char *cond = conditions[hash_string(location) % 5];
        int humidity = 40 + (hash_string(location) % 40);
        int wind_kph = 5 + (hash_string(location) % 30);

        /* Add to feed panel */
        char panel_title[256];
        snprintf(panel_title, sizeof panel_title, "Weather — %s", location);
        lmux_feed_panel *panel = lmux_feed_panel_create(app, panel_title, "weather");
        if (panel) {
            char text[512];
            snprintf(text, sizeof text, "[%s] %s %d°C, Humidity: %d%%, Wind: %dkm/h",
                location, cond, temp_c, humidity, wind_kph);
            lmux_feed_entry_add(app, panel->id, "weather", "weather", text);
        }

        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"location\":\"%s\",\"temperature\":%d,"
            "\"condition\":\"%s\",\"humidity\":%d,\"wind_kph\":%d}}",
            JESC(location), temp_c, cond, humidity, wind_kph);
        goto done;
    }

    /* --- weather.set_location --- */
    if (strcmp(cmd, "weather.set_location") == 0) {
        char location[256] = {0};
        json_extract_string(args_json, "location", location, sizeof location);
        if (!location[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing location\"}}");
            goto done;
        }
        lmux_event_push(app, "weather.location_set", "weather", "\"location\":\"%s\"", location);
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"location\":\"%s\"}}", JESC(location));
        goto done;
    }

    /* --- weather.refresh --- */
    if (strcmp(cmd, "weather.refresh") == 0) {
        /* Refresh is a no-op for simulated weather */
        lmux_event_push(app, "weather.refreshed", "weather", "{}");
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"status\":\"refreshed\"}}");
        goto done;
    }

    /* --- clipboard.copy --- */
    if (strcmp(cmd, "clipboard.copy") == 0) {
        char text[2048] = {0};
        json_extract_string(args_json, "text", text, sizeof text);
        if (!text[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing text\"}}");
            goto done;
        }
        int idx = app->clipboard_head;
        snprintf(app->clipboard[idx], sizeof app->clipboard[idx], "%s", text);
        app->clipboard_head = (app->clipboard_head + 1) % 100;
        if (app->clipboard_count < 100) app->clipboard_count++;
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"index\":%d,\"count\":%d}}",
            idx, app->clipboard_count);
        goto done;
    }

    /* --- clipboard.paste --- */
    if (strcmp(cmd, "clipboard.paste") == 0) {
        if (app->clipboard_count == 0) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"empty\",\"message\":\"clipboard is empty\"}}");
            goto done;
        }
        int index = -1; /* -1 means most recent */
        /* Extract index parameter */
        const char *ip = strstr(args_json, "\"index\"");
        if (ip) {
            ip += 7;
            while (*ip == ' ' || *ip == ':') ip++;
            index = atoi(ip);
        }
        int actual;
        if (index < 0) {
            /* Most recent = the slot before head */
            actual = (app->clipboard_head - 1 + 100) % 100;
        } else {
            if (index >= app->clipboard_count) {
                written = snprintf(result, result_cap,
                    "{\"ok\":false,\"error\":{\"code\":\"invalid_index\",\"message\":\"index out of range\"}}");
                goto done;
            }
            /* Convert logical index to physical: 0 = oldest */
            actual = (app->clipboard_head - app->clipboard_count + index + 100) % 100;
        }
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"text\":\"%s\",\"index\":%d}}",
            JESC(app->clipboard[actual]), index < 0 ? app->clipboard_count - 1 : index);
        goto done;
    }

    /* --- clipboard.list --- */
    if (strcmp(cmd, "clipboard.list") == 0) {
        char buf[32768];
        int n = snprintf(buf, sizeof buf,
            "{\"ok\":true,\"result\":{\"entries\":[");
        int start = (app->clipboard_head - app->clipboard_count + 100) % 100;
        for (int i = 0; i < app->clipboard_count; i++) {
            int idx = (start + i) % 100;
            if (i > 0) n += snprintf(buf + n, sizeof buf - n, ",");
            n += snprintf(buf + n, sizeof buf - n,
                "{\"index\":%d,\"text\":\"%s\"}", i, JESC(app->clipboard[idx]));
        }
        n += snprintf(buf + n, sizeof buf - n, "],\"count\":%d}}", app->clipboard_count);
        written = snprintf(result, result_cap, "%s", buf);
        goto done;
    }

    /* --- clipboard.clear --- */
    if (strcmp(cmd, "clipboard.clear") == 0) {
        app->clipboard_count = 0;
        app->clipboard_head = 0;
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- template.list --- */
    if (strcmp(cmd, "template.list") == 0) {
        /* Built-in templates */
        const char *builtins[] = {"development", "devops", "data-science", "web-dev", "minimal"};
        int nbuiltin = 5;
        char buf[4096];
        int n = snprintf(buf, sizeof buf,
            "{\"ok\":true,\"result\":{\"templates\":[");
        for (int i = 0; i < nbuiltin; i++) {
            if (i > 0) n += snprintf(buf + n, sizeof buf - n, ",");
            n += snprintf(buf + n, sizeof buf - n,
                "{\"name\":\"%s\",\"type\":\"builtin\"}", builtins[i]);
        }
        for (int i = 0; i < app->template_count; i++) {
            n += snprintf(buf + n, sizeof buf - n, ",");
            n += snprintf(buf + n, sizeof buf - n,
                "{\"name\":\"%s\",\"type\":\"custom\"}", app->templates[i]);
        }
        n += snprintf(buf + n, sizeof buf - n, "]}}");
        written = snprintf(result, result_cap, "%s", buf);
        goto done;
    }

    /* --- template.create --- */
    if (strcmp(cmd, "template.create") == 0) {
        char name[64] = {0};
        json_extract_string(args_json, "template", name, sizeof name);
        if (!name[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing template name\"}}");
            goto done;
        }

        /* Check if it's a built-in or custom template */
        bool found = false;
        const char *builtins[] = {"development", "devops", "data-science", "web-dev", "minimal"};
        for (int i = 0; i < 5; i++) {
            if (strcmp(name, builtins[i]) == 0) { found = true; break; }
        }
        if (!found) {
            for (int i = 0; i < app->template_count; i++) {
                if (strcmp(name, app->templates[i]) == 0) { found = true; break; }
            }
        }
        if (!found) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"template not found\"}}");
            goto done;
        }

        /* Create workspace with template-appropriate title */
        char ws_title[256];
        snprintf(ws_title, sizeof ws_title, "%s workspace", name);
        lmux_workspace *ws = lmux_workspace_create(app, ws_title);
        if (!ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"create_failed\",\"message\":\"could not create workspace\"}}");
            goto done;
        }

        /* Add template-specific surfaces */
        if (strcmp(name, "development") == 0 || strcmp(name, "devops") == 0) {
            /* Dev template: main + terminal + logs */
            lmux_surface *s2 = surface_new(ws);
            snprintf(s2->title, sizeof s2->title, "Terminal");
            vec_push(&ws->surfaces, s2);
            lmux_pane *p2 = pane_new(ws, s2, "terminal");
            vec_push(&s2->panes, p2);

            lmux_surface *s3 = surface_new(ws);
            snprintf(s3->title, sizeof s3->title, "Logs");
            vec_push(&ws->surfaces, s3);
            lmux_pane *p3 = pane_new(ws, s3, "terminal");
            vec_push(&s3->panes, p3);
        } else if (strcmp(name, "web-dev") == 0) {
            /* Web dev: editor + browser + terminal */
            lmux_surface *s2 = surface_new(ws);
            snprintf(s2->title, sizeof s2->title, "Browser");
            vec_push(&ws->surfaces, s2);
            lmux_pane *p2 = pane_new(ws, s2, "terminal");
            vec_push(&s2->panes, p2);

            lmux_surface *s3 = surface_new(ws);
            snprintf(s3->title, sizeof s3->title, "Terminal");
            vec_push(&ws->surfaces, s3);
            lmux_pane *p3 = pane_new(ws, s3, "terminal");
            vec_push(&s3->panes, p3);
        }

        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"workspace_id\":%u,\"title\":\"%s\",\"template\":\"%s\"}}",
            ws->id, JESC(ws_title), JESC(name));
        goto done;
    }

    /* --- template.save --- */
    if (strcmp(cmd, "template.save") == 0) {
        char name[64] = {0};
        json_extract_string(args_json, "name", name, sizeof name);
        if (!name[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing name\"}}");
            goto done;
        }
        if (app->template_count >= 32) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"limit_reached\",\"message\":\"too many templates\"}}");
            goto done;
        }
        snprintf(app->templates[app->template_count], sizeof app->templates[0], "%s", name);
        app->template_count++;
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"name\":\"%s\"}}", JESC(name));
        goto done;
    }

    /* --- template.delete --- */
    if (strcmp(cmd, "template.delete") == 0) {
        char name[64] = {0};
        json_extract_string(args_json, "name", name, sizeof name);
        if (!name[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing name\"}}");
            goto done;
        }
        /* Cannot delete built-in templates */
        const char *builtins[] = {"development", "devops", "data-science", "web-dev", "minimal"};
        for (int i = 0; i < 5; i++) {
            if (strcmp(name, builtins[i]) == 0) {
                written = snprintf(result, result_cap,
                    "{\"ok\":false,\"error\":{\"code\":\"forbidden\",\"message\":\"cannot delete built-in template\"}}");
                goto done;
            }
        }
        /* Find and remove custom template */
        bool found = false;
        for (int i = 0; i < app->template_count; i++) {
            if (strcmp(name, app->templates[i]) == 0) {
                /* Shift remaining templates */
                for (int j = i; j < app->template_count - 1; j++) {
                    snprintf(app->templates[j], sizeof app->templates[0], "%s", app->templates[j + 1]);
                }
                app->template_count--;
                found = true;
                break;
            }
        }
        if (!found) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"template not found\"}}");
            goto done;
        }
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- profile.status --- */
    if (strcmp(cmd, "profile.status") == 0) {
        long elapsed = 0;
        if (app->profile_active) {
            struct timespec now;
            clock_gettime(CLOCK_MONOTONIC, &now);
            elapsed = (now.tv_sec - app->profile_start.tv_sec) * 1000 +
                      (now.tv_nsec - app->profile_start.tv_nsec) / 1000000;
        }
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"active\":%s,\"label\":\"%s\",\"elapsed_ms\":%ld}}",
            app->profile_active ? "true" : "false",
            JESC(app->profile_label), elapsed);
        goto done;
    }

    /* --- profile.start --- */
    if (strcmp(cmd, "profile.start") == 0) {
        char label[128] = {0};
        json_extract_string(args_json, "label", label, sizeof label);
        if (!label[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing label\"}}");
            goto done;
        }
        if (app->profile_active) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"already_active\",\"message\":\"profiling already active\"}}");
            goto done;
        }
        app->profile_active = true;
        snprintf(app->profile_label, sizeof app->profile_label, "%s", label);
        clock_gettime(CLOCK_MONOTONIC, &app->profile_start);
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"label\":\"%s\"}}", JESC(label));
        goto done;
    }

    /* --- profile.stop --- */
    if (strcmp(cmd, "profile.stop") == 0) {
        if (!app->profile_active) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_active\",\"message\":\"no profiling session active\"}}");
            goto done;
        }
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        long elapsed = (now.tv_sec - app->profile_start.tv_sec) * 1000 +
                      (now.tv_nsec - app->profile_start.tv_nsec) / 1000000;

        /* Store result */
        int idx = app->profile_result_count % 32;
        snprintf(app->profile_results[idx].label, sizeof app->profile_results[idx].label,
            "%s", app->profile_label);
        app->profile_results[idx].elapsed_ms = elapsed;
        app->profile_result_count++;

        app->profile_active = false;
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"label\":\"%s\",\"elapsed_ms\":%ld}}",
            JESC(app->profile_label), elapsed);
        goto done;
    }

    /* --- profile.list --- */
    if (strcmp(cmd, "profile.list") == 0) {
        char buf[8192];
        int n = snprintf(buf, sizeof buf,
            "{\"ok\":true,\"result\":{\"results\":[");
        int count = app->profile_result_count < 32 ? app->profile_result_count : 32;
        int start = (app->profile_result_count - count + 32) % 32;
        for (int i = 0; i < count; i++) {
            int idx = (start + i) % 32;
            if (i > 0) n += snprintf(buf + n, sizeof buf - n, ",");
            n += snprintf(buf + n, sizeof buf - n,
                "{\"label\":\"%s\",\"elapsed_ms\":%ld}",
                JESC(app->profile_results[idx].label),
                app->profile_results[idx].elapsed_ms);
        }
        n += snprintf(buf + n, sizeof buf - n, "],\"count\":%d}}", count);
        written = snprintf(result, result_cap, "%s", buf);
        goto done;
    }

    /* --- cloud.vms.list --- */
    if (strcmp(cmd, "cloud.vms.list") == 0) {
        char provider[32] = {0};
        json_extract_string(args_json, "provider", provider, sizeof provider);
        char buf[8192];
        int n = snprintf(buf, sizeof buf,
            "{\"ok\":true,\"result\":{\"vms\":[");
        int count = 0;
        for (int i = 0; i < app->vm_count; i++) {
            if (provider[0] && strcmp(provider, app->vms[i].provider) != 0) continue;
            if (count > 0) n += snprintf(buf + n, sizeof buf - n, ",");
            n += snprintf(buf + n, sizeof buf - n,
                "{\"id\":\"%s\",\"name\":\"%s\",\"provider\":\"%s\",\"size\":\"%s\",\"status\":\"%s\"}",
                app->vms[i].id, app->vms[i].name, app->vms[i].provider,
                app->vms[i].size, app->vms[i].status);
            count++;
        }
        n += snprintf(buf + n, sizeof buf - n, "],\"count\":%d}}", count);
        written = snprintf(result, result_cap, "%s", buf);
        goto done;
    }

    /* --- cloud.vms.create --- */
    if (strcmp(cmd, "cloud.vms.create") == 0) {
        char name[64] = {0}, provider[32] = {0}, size[32] = {0};
        json_extract_string(args_json, "name", name, sizeof name);
        json_extract_string(args_json, "provider", provider, sizeof provider);
        json_extract_string(args_json, "size", size, sizeof size);
        if (!name[0] || !provider[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing name or provider\"}}");
            goto done;
        }
        if (app->vm_count >= 32) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"limit_reached\",\"message\":\"too many VMs\"}}");
            goto done;
        }
        /* Check for duplicate name */
        for (int i = 0; i < app->vm_count; i++) {
            if (strcmp(name, app->vms[i].name) == 0) {
                written = snprintf(result, result_cap,
                    "{\"ok\":false,\"error\":{\"code\":\"already_exists\",\"message\":\"VM name already exists\"}}");
                goto done;
            }
        }
        int idx = app->vm_count;
        snprintf(app->vms[idx].id, sizeof app->vms[idx].id, "vm-%d", idx + 1);
        snprintf(app->vms[idx].name, sizeof app->vms[idx].name, "%s", name);
        snprintf(app->vms[idx].provider, sizeof app->vms[idx].provider, "%s", provider);
        snprintf(app->vms[idx].size, sizeof app->vms[idx].size, "%s", size[0] ? size : "e2-medium");
        snprintf(app->vms[idx].status, sizeof app->vms[idx].status, "running");
        app->vm_count++;
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"vm_id\":\"%s\",\"name\":\"%s\",\"provider\":\"%s\",\"status\":\"running\"}}",
            app->vms[idx].id, JESC(name), JESC(provider));
        goto done;
    }

    /* --- cloud.vms.destroy --- */
    if (strcmp(cmd, "cloud.vms.destroy") == 0) {
        char vm_id[64] = {0};
        json_extract_string(args_json, "vm_id", vm_id, sizeof vm_id);
        if (!vm_id[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing vm_id\"}}");
            goto done;
        }
        bool found = false;
        for (int i = 0; i < app->vm_count; i++) {
            if (strcmp(vm_id, app->vms[i].id) == 0) {
                /* Shift remaining VMs */
                for (int j = i; j < app->vm_count - 1; j++) {
                    memcpy(&app->vms[j], &app->vms[j + 1], sizeof app->vms[0]);
                }
                app->vm_count--;
                found = true;
                break;
            }
        }
        if (!found) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"VM not found\"}}");
            goto done;
        }
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- cloud.vms.ssh --- */
    if (strcmp(cmd, "cloud.vms.ssh") == 0) {
        char vm_id[64] = {0};
        json_extract_string(args_json, "vm_id", vm_id, sizeof vm_id);
        if (!vm_id[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing vm_id\"}}");
            goto done;
        }
        bool found = false;
        for (int i = 0; i < app->vm_count; i++) {
            if (strcmp(vm_id, app->vms[i].id) == 0) {
                found = true;
                /* Create a pane for SSH connection */
                if (app->workspaces.len == 0) {
                    written = snprintf(result, result_cap,
                        "{\"ok\":false,\"error\":{\"code\":\"no_workspace\",\"message\":\"no active workspace\"}}");
                    goto done;
                }
                lmux_workspace *ws = app->ws_current;
                if (!ws) {
                    written = snprintf(result, result_cap,
                        "{\"ok\":false,\"error\":{\"code\":\"no_workspace\",\"message\":\"no active workspace\"}}");
                    goto done;
                }
                lmux_surface *surf = lmux_surface_focused(ws);
                if (!surf) {
                    written = snprintf(result, result_cap,
                        "{\"ok\":false,\"error\":{\"code\":\"no_surface\",\"message\":\"no active surface\"}}");
                    goto done;
                }
                lmux_pane *pane = pane_new(ws, surf, "terminal");
                vec_push(&surf->panes, pane);
                written = snprintf(result, result_cap,
                    "{\"ok\":true,\"result\":{\"pane_id\":%u,\"vm_id\":\"%s\",\"host\":\"%s\"}}",
                    pane->id, vm_id, app->vms[i].name);
                goto done;
            }
        }
        if (!found) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"VM not found\"}}");
            goto done;
        }
        goto done;
    }

    /* --- companion.register --- */
    if (strcmp(cmd, "companion.register") == 0) {
        char device_id[64] = {0}, device_name[64] = {0}, platform[16] = {0};
        json_extract_string(args_json, "device_id", device_id, sizeof device_id);
        json_extract_string(args_json, "device_name", device_name, sizeof device_name);
        json_extract_string(args_json, "platform", platform, sizeof platform);
        if (!device_id[0] || !device_name[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing device_id or device_name\"}}");
            goto done;
        }
        if (app->companion_count >= 16) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"limit_reached\",\"message\":\"too many companions\"}}");
            goto done;
        }
        /* Check for duplicate */
        for (int i = 0; i < app->companion_count; i++) {
            if (strcmp(device_id, app->companions[i].id) == 0) {
                /* Update existing */
                snprintf(app->companions[i].name, sizeof app->companions[i].name, "%s", device_name);
                snprintf(app->companions[i].platform, sizeof app->companions[i].platform, "%s", platform[0] ? platform : "ios");
                app->companions[i].connected = true;
                clock_gettime(CLOCK_MONOTONIC, (struct timespec *)&app->companions[i].last_seen);
                written = snprintf(result, result_cap,
                    "{\"ok\":true,\"result\":{\"companion_id\":\"%s\",\"status\":\"updated\"}}", device_id);
                goto done;
            }
        }
        int idx = app->companion_count;
        snprintf(app->companions[idx].id, sizeof app->companions[idx].id, "%s", device_id);
        snprintf(app->companions[idx].name, sizeof app->companions[idx].name, "%s", device_name);
        snprintf(app->companions[idx].platform, sizeof app->companions[idx].platform, "%s", platform[0] ? platform : "ios");
        app->companions[idx].connected = true;
        clock_gettime(CLOCK_MONOTONIC, (struct timespec *)&app->companions[idx].last_seen);
        app->companion_count++;
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"companion_id\":\"%s\",\"status\":\"registered\"}}", device_id);
        goto done;
    }

    /* --- companion.status --- */
    if (strcmp(cmd, "companion.status") == 0) {
        char companion_id[64] = {0};
        json_extract_string(args_json, "companion_id", companion_id, sizeof companion_id);
        char buf[4096];
        int n = snprintf(buf, sizeof buf,
            "{\"ok\":true,\"result\":{\"companions\":[");
        int count = 0;
        for (int i = 0; i < app->companion_count; i++) {
            if (companion_id[0] && strcmp(companion_id, app->companions[i].id) != 0) continue;
            if (count > 0) n += snprintf(buf + n, sizeof buf - n, ",");
            n += snprintf(buf + n, sizeof buf - n,
                "{\"id\":\"%s\",\"name\":\"%s\",\"platform\":\"%s\",\"connected\":%s}",
                app->companions[i].id, app->companions[i].name, app->companions[i].platform,
                app->companions[i].connected ? "true" : "false");
            count++;
        }
        n += snprintf(buf + n, sizeof buf - n, "],\"count\":%d}}", count);
        written = snprintf(result, result_cap, "%s", buf);
        goto done;
    }

    /* --- companion.notify --- */
    if (strcmp(cmd, "companion.notify") == 0) {
        char companion_id[64] = {0}, title[256] = {0}, message[1024] = {0};
        json_extract_string(args_json, "companion_id", companion_id, sizeof companion_id);
        json_extract_string(args_json, "title", title, sizeof title);
        json_extract_string(args_json, "message", message, sizeof message);
        if (!companion_id[0] || !title[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing companion_id or title\"}}");
            goto done;
        }
        bool found = false;
        for (int i = 0; i < app->companion_count; i++) {
            if (strcmp(companion_id, app->companions[i].id) == 0) {
                found = true;
                /* In production, this would send push notification */
                written = snprintf(result, result_cap,
                    "{\"ok\":true,\"result\":{\"companion_id\":\"%s\",\"title\":\"%s\",\"status\":\"sent\"}}",
                    companion_id, JESC(title));
                goto done;
            }
        }
        if (!found) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"companion not found\"}}");
            goto done;
        }
        goto done;
    }

    /* --- companion.unregister --- */
    if (strcmp(cmd, "companion.unregister") == 0) {
        char companion_id[64] = {0};
        json_extract_string(args_json, "companion_id", companion_id, sizeof companion_id);
        if (!companion_id[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing companion_id\"}}");
            goto done;
        }
        bool found = false;
        for (int i = 0; i < app->companion_count; i++) {
            if (strcmp(companion_id, app->companions[i].id) == 0) {
                for (int j = i; j < app->companion_count - 1; j++) {
                    memcpy(&app->companions[j], &app->companions[j + 1], sizeof app->companions[0]);
                }
                app->companion_count--;
                found = true;
                break;
            }
        }
        if (!found) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"companion not found\"}}");
            goto done;
        }
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- team.create --- */
    if (strcmp(cmd, "team.create") == 0) {
        char name[64] = {0};
        json_extract_string(args_json, "name", name, sizeof name);
        if (!name[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing name\"}}");
            goto done;
        }
        if (app->team_count >= 16) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"limit_reached\",\"message\":\"too many teams\"}}");
            goto done;
        }
        int idx = app->team_count;
        snprintf(app->teams[idx].id, sizeof app->teams[idx].id, "team-%d", idx + 1);
        snprintf(app->teams[idx].name, sizeof app->teams[idx].name, "%s", name);
        app->teams[idx].member_count = 0;
        app->team_count++;
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"team_id\":\"%s\",\"name\":\"%s\"}}",
            app->teams[idx].id, JESC(name));
        goto done;
    }

    /* --- team.add --- */
    if (strcmp(cmd, "team.add") == 0) {
        char team_id[64] = {0}, agent_name[64] = {0}, role[32] = {0};
        json_extract_string(args_json, "team_id", team_id, sizeof team_id);
        json_extract_string(args_json, "agent_name", agent_name, sizeof agent_name);
        json_extract_string(args_json, "role", role, sizeof role);
        if (!team_id[0] || !agent_name[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing team_id or agent_name\"}}");
            goto done;
        }
        bool found = false;
        for (int i = 0; i < app->team_count; i++) {
            if (strcmp(team_id, app->teams[i].id) == 0) {
                if (app->teams[i].member_count >= 8) {
                    written = snprintf(result, result_cap,
                        "{\"ok\":false,\"error\":{\"code\":\"limit_reached\",\"message\":\"team is full\"}}");
                    goto done;
                }
                int midx = app->teams[i].member_count;
                snprintf(app->teams[i].members[midx].name, sizeof app->teams[i].members[midx].name, "%s", agent_name);
                snprintf(app->teams[i].members[midx].role, sizeof app->teams[i].members[midx].role, "%s", role[0] ? role : "member");
                app->teams[i].member_count++;
                found = true;
                written = snprintf(result, result_cap,
                    "{\"ok\":true,\"result\":{\"team_id\":\"%s\",\"agent_name\":\"%s\",\"role\":\"%s\"}}",
                    team_id, JESC(agent_name), JESC(role[0] ? role : "member"));
                goto done;
            }
        }
        if (!found) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"team not found\"}}");
            goto done;
        }
        goto done;
    }

    /* --- team.remove --- */
    if (strcmp(cmd, "team.remove") == 0) {
        char team_id[64] = {0}, agent_name[64] = {0};
        json_extract_string(args_json, "team_id", team_id, sizeof team_id);
        json_extract_string(args_json, "agent_name", agent_name, sizeof agent_name);
        if (!team_id[0] || !agent_name[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing team_id or agent_name\"}}");
            goto done;
        }
        bool found = false;
        for (int i = 0; i < app->team_count; i++) {
            if (strcmp(team_id, app->teams[i].id) == 0) {
                for (int j = 0; j < app->teams[i].member_count; j++) {
                    if (strcmp(agent_name, app->teams[i].members[j].name) == 0) {
                        for (int k = j; k < app->teams[i].member_count - 1; k++) {
                            memcpy(&app->teams[i].members[k], &app->teams[i].members[k + 1],
                                sizeof app->teams[i].members[0]);
                        }
                        app->teams[i].member_count--;
                        found = true;
                        break;
                    }
                }
                break;
            }
        }
        if (!found) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"team or agent not found\"}}");
            goto done;
        }
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- team.list --- */
    if (strcmp(cmd, "team.list") == 0) {
        char team_id[64] = {0};
        json_extract_string(args_json, "team_id", team_id, sizeof team_id);
        char buf[8192];
        int n = snprintf(buf, sizeof buf,
            "{\"ok\":true,\"result\":{\"teams\":[");
        int count = 0;
        for (int i = 0; i < app->team_count; i++) {
            if (team_id[0] && strcmp(team_id, app->teams[i].id) != 0) continue;
            if (count > 0) n += snprintf(buf + n, sizeof buf - n, ",");
            n += snprintf(buf + n, sizeof buf - n,
                "{\"id\":\"%s\",\"name\":\"%s\",\"member_count\":%d",
                app->teams[i].id, app->teams[i].name, app->teams[i].member_count);
            /* Include members if listing specific team */
            if (team_id[0]) {
                n += snprintf(buf + n, sizeof buf - n, ",\"members\":[");
                for (int j = 0; j < app->teams[i].member_count; j++) {
                    if (j > 0) n += snprintf(buf + n, sizeof buf - n, ",");
                    n += snprintf(buf + n, sizeof buf - n,
                        "{\"name\":\"%s\",\"role\":\"%s\"}",
                        app->teams[i].members[j].name, app->teams[i].members[j].role);
                }
                n += snprintf(buf + n, sizeof buf - n, "]");
            }
            n += snprintf(buf + n, sizeof buf - n, "}");
            count++;
        }
        n += snprintf(buf + n, sizeof buf - n, "],\"count\":%d}}", count);
        written = snprintf(result, result_cap, "%s", buf);
        goto done;
    }

    /* --- team.dispatch --- */
    if (strcmp(cmd, "team.dispatch") == 0) {
        char team_id[64] = {0}, task[1024] = {0};
        json_extract_string(args_json, "team_id", team_id, sizeof team_id);
        json_extract_string(args_json, "task", task, sizeof task);
        if (!team_id[0] || !task[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing team_id or task\"}}");
            goto done;
        }
        bool found = false;
        for (int i = 0; i < app->team_count; i++) {
            if (strcmp(team_id, app->teams[i].id) == 0) {
                found = true;
                /* Generate dispatch ID */
                char dispatch_id[64];
                snprintf(dispatch_id, sizeof dispatch_id, "dispatch-%d-%d", i + 1, app->team_count);
                written = snprintf(result, result_cap,
                    "{\"ok\":true,\"result\":{\"dispatch_id\":\"%s\",\"team_id\":\"%s\",\"task\":\"%s\",\"status\":\"dispatched\"}}",
                    dispatch_id, team_id, JESC(task));
                goto done;
            }
        }
        if (!found) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"team not found\"}}");
            goto done;
        }
        goto done;
    }

    /* --- team.delete --- */
    if (strcmp(cmd, "team.delete") == 0) {
        char team_id[64] = {0};
        json_extract_string(args_json, "team_id", team_id, sizeof team_id);
        if (!team_id[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing team_id\"}}");
            goto done;
        }
        bool found = false;
        for (int i = 0; i < app->team_count; i++) {
            if (strcmp(team_id, app->teams[i].id) == 0) {
                for (int j = i; j < app->team_count - 1; j++) {
                    memcpy(&app->teams[j], &app->teams[j + 1], sizeof app->teams[0]);
                }
                app->team_count--;
                found = true;
                break;
            }
        }
        if (!found) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"team not found\"}}");
            goto done;
        }
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- browser.import --- */
    if (strcmp(cmd, "browser.import") == 0) {
        char source[64] = {0}, path[1024] = {0};
        json_extract_string(args_json, "source", source, sizeof source);
        json_extract_string(args_json, "path", path, sizeof path);

        if (!source[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing source\"}}");
            goto done;
        }
        if (!path[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing path\"}}");
            goto done;
        }

        /* Read file */
        FILE *fp = fopen(path, "r");
        if (!fp) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"file_error\",\"message\":\"cannot open file\"}}");
            goto done;
        }
        fseek(fp, 0, SEEK_END);
        long fsize = ftell(fp);
        fseek(fp, 0, SEEK_SET);
        if (fsize <= 0 || fsize > 1024 * 1024) {
            fclose(fp);
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"file_error\",\"message\":\"file too large or empty\"}}");
            goto done;
        }
        char *fbuf = malloc(fsize + 1);
        if (!fbuf) { fclose(fp); goto done; }
        (void)fread(fbuf, 1, fsize, fp);
        fbuf[fsize] = '\0';
        fclose(fp);

        /* Create feed panel */
        char panel_title[256];
        snprintf(panel_title, sizeof panel_title, "Imported from %s", source);
        lmux_feed_panel *panel = lmux_feed_panel_create(app, panel_title, "bookmark");
        if (!panel) {
            free(fbuf);
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"create_failed\",\"message\":\"could not create panel\"}}");
            goto done;
        }

        int count = 0;

        /* Parse bookmarks — support simple JSON array and Chrome format */
        if (strcmp(source, "chrome") == 0) {
            /* Chrome format: {roots:{bookmark_bar:{children:[...]}}} */
            /* Simple substring search for "url" fields in the JSON */
            const char *p = fbuf;
            while ((p = strstr(p, "\"url\"")) != NULL) {
                p += 5;
                /* Skip : and whitespace */
                while (*p == ' ' || *p == ':') p++;
                if (*p == '"') {
                    p++;
                    const char *end = strchr(p, '"');
                    if (end && end - p < 1024) {
                        char url[1024] = {0};
                        snprintf(url, (size_t)(end - p) + 1, "%s", p);
                        /* Find closest "name" before this url */
                        char name[256] = {0};
                        const char *name_search = fbuf;
                        const char *last_name = NULL;
                        while (name_search < p - 5) {
                            const char *n = strstr(name_search, "\"name\"");
                            if (!n || n >= p - 5) break;
                            last_name = n;
                            name_search = n + 6;
                        }
                        if (last_name) {
                            const char *np = last_name + 6;
                            while (*np == ' ' || *np == ':') np++;
                            if (*np == '"') {
                                np++;
                                const char *nend = strchr(np, '"');
                                if (nend && nend - np < 256)
                                    snprintf(name, (size_t)(nend - np) + 1, "%s", np);
                            }
                        }
                        if (!name[0]) snprintf(name, sizeof name, "Bookmark");

                        char text[1280];
                        snprintf(text, sizeof text, "[%s] %s", name, url);
                        lmux_feed_entry_add(app, panel->id, "bookmark", "chrome", text);
                        count++;
                    }
                }
            }
        } else {
            /* Simple format: [{title, url, folder}, ...] */
            const char *p = fbuf;
            while ((p = strstr(p, "\"url\"")) != NULL) {
                p += 5;
                while (*p == ' ' || *p == ':') p++;
                if (*p == '"') {
                    p++;
                    const char *end = strchr(p, '"');
                    if (end && end - p < 1024) {
                        char url[1024] = {0};
                        snprintf(url, (size_t)(end - p) + 1, "%s", p);

                        char title[256] = {0};
                        const char *ts = strstr(fbuf, "\"title\"");
                        if (ts && ts < p - 5) {
                            const char *tp = ts + 7;
                            while (*tp == ' ' || *tp == ':') tp++;
                            if (*tp == '"') {
                                tp++;
                                const char *tend = strchr(tp, '"');
                                if (tend && tend - tp < 256)
                                    snprintf(title, (size_t)(tend - tp) + 1, "%s", tp);
                            }
                        }
                        if (!title[0]) snprintf(title, sizeof title, "Bookmark");

                        char text[1280];
                        snprintf(text, sizeof text, "[%s] %s", title, url);
                        lmux_feed_entry_add(app, panel->id, "bookmark",
                            source[0] ? source : "json", text);
                        count++;
                    }
                }
            }
        }

        free(fbuf);
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"panel_id\":%u,\"count\":%d}}",
            panel->id, count);
        goto done;
    }

    /* --- window commands --- */
    if (strcmp(cmd, "window.create") == 0) {
        char title[256] = {0};
        json_extract_string(args_json, "title", title, sizeof title);
        lmux_window *w = lmux_window_create(app, title[0] ? title : NULL);
        if (w) {
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"window_id\":%u,\"title\":\"%s\"}}",
                w->id, w->title);
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"create_failed\",\"message\":\"could not create window\"}}");
        }
        goto done;
    }

    if (strcmp(cmd, "window.list") == 0) {
        char buf[8192];
        int n = snprintf(buf, sizeof buf, "{\"ok\":true,\"result\":{\"windows\":[");
        for (size_t i = 0; i < app->windows.len; i++) {
            lmux_window *w = app->windows.items[i];
            if (i > 0) n += snprintf(buf + n, sizeof buf - n, ",");
            n += snprintf(buf + n, sizeof buf - n,
                "{\"id\":%u,\"title\":\"%s\",\"workspace_count\":%zu}",
                w->id, w->title, w->workspace_count);
        }
        n += snprintf(buf + n, sizeof buf - n, "]}}");
        written = snprintf(result, result_cap, "%s", buf);
        goto done;
    }

    if (strcmp(cmd, "window.close") == 0) {
        char w_id[64] = {0};
        json_extract_string(args_json, "id", w_id, sizeof w_id);
        if (!w_id[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing id\"}}");
            goto done;
        }
        lmux_id wid = (lmux_id)atol(w_id);
        if (lmux_window_by_id(app, wid)) {
            lmux_window_close(app, wid);
            written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"window not found\"}}");
        }
        goto done;
    }

    if (strcmp(cmd, "window.focus") == 0) {
        char w_id[64] = {0};
        json_extract_string(args_json, "id", w_id, sizeof w_id);
        if (!w_id[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing id\"}}");
            goto done;
        }
        lmux_id wid = (lmux_id)atol(w_id);
        if (lmux_window_focus(app, wid)) {
            written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"window not found\"}}");
        }
        goto done;
    }

    if (strcmp(cmd, "window.move-workspace") == 0 || strcmp(cmd, "window.move_workspace") == 0) {
        char w_id[64] = {0}, ws_id[64] = {0};
        json_extract_string(args_json, "window_id", w_id, sizeof w_id);
        json_extract_string(args_json, "workspace_id", ws_id, sizeof ws_id);
        if (!w_id[0] || !ws_id[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing window_id or workspace_id\"}}");
            goto done;
        }
        lmux_id wid = (lmux_id)atol(w_id);
        lmux_id wsid = (lmux_id)atol(ws_id);
        if (lmux_window_move_workspace(app, wid, wsid)) {
            written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"move_failed\",\"message\":\"could not move workspace\"}}");
        }
        goto done;
    }

    /* --- SSH session commands --- */
    if (strcmp(cmd, "ssh.session.create") == 0) {
        char host[256] = {0}, user[128] = {0}, key_path[512] = {0};
        json_extract_string(args_json, "host", host, sizeof host);
        json_extract_string(args_json, "user", user, sizeof user);
        json_extract_string(args_json, "key_path", key_path, sizeof key_path);
        if (!host[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing host\"}}");
            goto done;
        }
        lmux_ssh_session *s = lmux_ssh_session_create(app, host,
            user[0] ? user : NULL, key_path[0] ? key_path : NULL);
        if (s) {
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"session_id\":%u,\"host\":\"%s\"}}",
                s->id, s->host);
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"create_failed\",\"message\":\"could not create session\"}}");
        }
        goto done;
    }

    if (strcmp(cmd, "ssh.session.list") == 0) {
        char buf[8192];
        int n = snprintf(buf, sizeof buf, "{\"ok\":true,\"result\":{\"sessions\":[");
        for (size_t i = 0; i < app->ssh_sessions.len; i++) {
            lmux_ssh_session *s = app->ssh_sessions.items[i];
            if (i > 0) n += snprintf(buf + n, sizeof buf - n, ",");
            n += snprintf(buf + n, sizeof buf - n,
                "{\"id\":%u,\"host\":\"%s\",\"user\":\"%s\",\"active\":%s}",
                s->id, s->host, s->user, s->active ? "true" : "false");
        }
        n += snprintf(buf + n, sizeof buf - n, "]}}");
        written = snprintf(result, result_cap, "%s", buf);
        goto done;
    }

    if (strcmp(cmd, "ssh.session.kill") == 0) {
        char s_id[64] = {0};
        json_extract_string(args_json, "id", s_id, sizeof s_id);
        if (!s_id[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing id\"}}");
            goto done;
        }
        lmux_id sid = (lmux_id)atol(s_id);
        if (lmux_ssh_session_kill(app, sid)) {
            written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"session not found\"}}");
        }
        goto done;
    }

    if (strcmp(cmd, "ssh.session.save") == 0) {
        char path[512] = {0};
        json_extract_string(args_json, "path", path, sizeof path);
        if (!path[0]) {
            const char *home = getenv("HOME");
            snprintf(path, sizeof path, "%s/.local/share/lmux/ssh_sessions.json",
                home ? home : "/tmp");
        }
        if (lmux_ssh_session_save(app, path)) {
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"path\":\"%s\"}}", path);
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"save_failed\",\"message\":\"could not save sessions\"}}");
        }
        goto done;
    }

    if (strcmp(cmd, "ssh.session.restore") == 0) {
        char path[512] = {0};
        json_extract_string(args_json, "path", path, sizeof path);
        if (!path[0]) {
            const char *home = getenv("HOME");
            snprintf(path, sizeof path, "%s/.local/share/lmux/ssh_sessions.json",
                home ? home : "/tmp");
        }
        if (lmux_ssh_session_restore(app, path)) {
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"path\":\"%s\"}}", path);
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"restore_failed\",\"message\":\"could not restore sessions\"}}");
        }
        goto done;
    }

    /* --- Feed panel commands --- */
    if (strcmp(cmd, "feed.panel.create") == 0) {
        char title[256] = {0}, filter[256] = {0};
        json_extract_string(args_json, "title", title, sizeof title);
        json_extract_string(args_json, "filter", filter, sizeof filter);
        lmux_feed_panel *p = lmux_feed_panel_create(app, title[0] ? title : NULL,
            filter[0] ? filter : NULL);
        if (p) {
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"panel_id\":%u,\"title\":\"%s\"}}",
                p->id, p->title);
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"create_failed\",\"message\":\"could not create panel\"}}");
        }
        goto done;
    }

    if (strcmp(cmd, "feed.panel.list") == 0) {
        char buf[8192];
        int n = snprintf(buf, sizeof buf, "{\"ok\":true,\"result\":{\"panels\":[");
        for (size_t i = 0; i < app->feed_panels.len; i++) {
            lmux_feed_panel *p = app->feed_panels.items[i];
            if (i > 0) n += snprintf(buf + n, sizeof buf - n, ",");
            n += snprintf(buf + n, sizeof buf - n,
                "{\"id\":%u,\"title\":\"%s\",\"filter\":\"%s\",\"entry_count\":%zu}",
                p->id, p->title, p->filter, lmux_feed_entry_count(app, p->id));
        }
        n += snprintf(buf + n, sizeof buf - n, "]}}");
        written = snprintf(result, result_cap, "%s", buf);
        goto done;
    }

    if (strcmp(cmd, "feed.panel.close") == 0) {
        char p_id[64] = {0};
        json_extract_string(args_json, "id", p_id, sizeof p_id);
        if (!p_id[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing id\"}}");
            goto done;
        }
        lmux_id pid = (lmux_id)atol(p_id);
        if (lmux_feed_panel_close(app, pid)) {
            written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"panel not found\"}}");
        }
        goto done;
    }

    if (strcmp(cmd, "feed.entry.add") == 0) {
        char p_id[64] = {0}, event_type[64] = {0}, source[128] = {0}, text[2048] = {0};
        json_extract_string(args_json, "panel_id", p_id, sizeof p_id);
        json_extract_string(args_json, "event_type", event_type, sizeof event_type);
        json_extract_string(args_json, "source", source, sizeof source);
        json_extract_string(args_json, "text", text, sizeof text);
        if (!p_id[0] || !text[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing panel_id or text\"}}");
            goto done;
        }
        lmux_id pid = (lmux_id)atol(p_id);
        if (lmux_feed_entry_add(app, pid, event_type[0] ? event_type : NULL,
            source[0] ? source : NULL, text)) {
            written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"add_failed\",\"message\":\"could not add entry\"}}");
        }
        goto done;
    }

    if (strcmp(cmd, "feed.entry.list") == 0) {
        char p_id[64] = {0};
        json_extract_string(args_json, "panel_id", p_id, sizeof p_id);
        if (!p_id[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing panel_id\"}}");
            goto done;
        }
        lmux_id pid = (lmux_id)atol(p_id);
        size_t count = lmux_feed_entry_count(app, pid);
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"count\":%zu}}", count);
        goto done;
    }

    if (strcmp(cmd, "feed.panel.clear") == 0) {
        char p_id[64] = {0};
        json_extract_string(args_json, "panel_id", p_id, sizeof p_id);
        if (!p_id[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing panel_id\"}}");
            goto done;
        }
        lmux_id pid = (lmux_id)atol(p_id);
        if (lmux_feed_panel_clear(app, pid)) {
            written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"clear_failed\",\"message\":\"could not clear panel\"}}");
        }
        goto done;
    }

    /* --- pane.copy_mode.enter --- */
    if (strcmp(cmd, "pane.copy_mode.enter") == 0) {
        lmux_workspace *ws = lmux_workspace_focused(app);
        if (!ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no workspaces\"}}");
            goto done;
        }
        lmux_pane *p = lmux_pane_focused(ws);
        if (!p) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no focused pane\"}}");
            goto done;
        }
        p->copy_mode_active = true;
        p->copy_cursor_row = 0;
        p->copy_cursor_col = 0;
        p->copy_selecting = false;
        p->copy_sel_start_row = 0;
        p->copy_sel_start_col = 0;
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"pane_id\":%u,\"active\":true}}", p->id);
        goto done;
    }

    /* --- pane.copy_mode.exit --- */
    if (strcmp(cmd, "pane.copy_mode.exit") == 0) {
        lmux_workspace *ws = lmux_workspace_focused(app);
        if (!ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no workspaces\"}}");
            goto done;
        }
        lmux_pane *p = lmux_pane_focused(ws);
        if (!p) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no focused pane\"}}");
            goto done;
        }
        bool was_active = p->copy_mode_active;
        p->copy_mode_active = false;
        p->copy_selecting = false;
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"pane_id\":%u,\"was_active\":%s}}",
            p->id, was_active ? "true" : "false");
        goto done;
    }

    /* --- pane.copy_mode.move --- */
    if (strcmp(cmd, "pane.copy_mode.move") == 0) {
        char key[16] = {0};
        json_extract_string(args_json, "key", key, sizeof key);
        lmux_workspace *ws = lmux_workspace_focused(app);
        if (!ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no workspaces\"}}");
            goto done;
        }
        lmux_pane *p = lmux_pane_focused(ws);
        if (!p) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no focused pane\"}}");
            goto done;
        }
        if (!p->copy_mode_active) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_state\",\"message\":\"copy mode not active\"}}");
            goto done;
        }
        if (!key[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing key\"}}");
            goto done;
        }
        /* Vi movement keys */
        if (strcmp(key, "h") == 0) {
            if (p->copy_cursor_col > 0) p->copy_cursor_col--;
        } else if (strcmp(key, "j") == 0) {
            p->copy_cursor_row++;
        } else if (strcmp(key, "k") == 0) {
            if (p->copy_cursor_row > 0) p->copy_cursor_row--;
        } else if (strcmp(key, "l") == 0) {
            p->copy_cursor_col++;
        } else if (strcmp(key, "w") == 0) {
            p->copy_cursor_col += 5; /* word jump (simplified) */
        } else if (strcmp(key, "b") == 0) {
            p->copy_cursor_col -= 5;
            if (p->copy_cursor_col < 0) p->copy_cursor_col = 0;
        } else if (strcmp(key, "0") == 0) {
            p->copy_cursor_col = 0;
        } else if (strcmp(key, "$") == 0) {
            p->copy_cursor_col = 9999; /* simplified: jump to end */
        } else if (strcmp(key, "gg") == 0) {
            p->copy_cursor_row = 0;
            p->copy_cursor_col = 0;
        } else if (strcmp(key, "G") == 0) {
            p->copy_cursor_row = 9999; /* simplified: jump to bottom */
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"unknown key: %s\"}}",
                key);
            goto done;
        }
        if (p->copy_cursor_row < 0) p->copy_cursor_row = 0;
        if (p->copy_cursor_col < 0) p->copy_cursor_col = 0;
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"pane_id\":%u,\"row\":%d,\"col\":%d}}",
            p->id, p->copy_cursor_row, p->copy_cursor_col);
        goto done;
    }

    /* --- pane.copy_mode.select_start --- */
    if (strcmp(cmd, "pane.copy_mode.select_start") == 0) {
        lmux_workspace *ws = lmux_workspace_focused(app);
        if (!ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no workspaces\"}}");
            goto done;
        }
        lmux_pane *p = lmux_pane_focused(ws);
        if (!p) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no focused pane\"}}");
            goto done;
        }
        if (!p->copy_mode_active) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_state\",\"message\":\"copy mode not active\"}}");
            goto done;
        }
        p->copy_selecting = true;
        p->copy_sel_start_row = p->copy_cursor_row;
        p->copy_sel_start_col = p->copy_cursor_col;
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"pane_id\":%u,\"selecting\":true,\"start_row\":%d,\"start_col\":%d}}",
            p->id, p->copy_sel_start_row, p->copy_sel_start_col);
        goto done;
    }

    /* --- pane.copy_mode.select_end --- */
    if (strcmp(cmd, "pane.copy_mode.select_end") == 0) {
        lmux_workspace *ws = lmux_workspace_focused(app);
        if (!ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no workspaces\"}}");
            goto done;
        }
        lmux_pane *p = lmux_pane_focused(ws);
        if (!p) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no focused pane\"}}");
            goto done;
        }
        if (!p->copy_mode_active) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_state\",\"message\":\"copy mode not active\"}}");
            goto done;
        }
        /* End selection: record end position (cursor position) */
        p->copy_selecting = false;
        int end_row = p->copy_cursor_row;
        int end_col = p->copy_cursor_col;
        int start_row = p->copy_sel_start_row;
        int start_col = p->copy_sel_start_col;
        /* Determine selection range (simplified: single-line) */
        if (start_row == end_row) {
            /* Same line: selection from min_col to max_col */
            int min_c = start_col < end_col ? start_col : end_col;
            int max_c = start_col > end_col ? start_col : end_col;
            (void)min_c; (void)max_c;
        }
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"pane_id\":%u,\"selecting\":false,"
            "\"start_row\":%d,\"start_col\":%d,\"end_row\":%d,\"end_col\":%d}}",
            p->id, start_row, start_col, end_row, end_col);
        goto done;
    }

    /* --- pane.copy_mode.yank --- */
    if (strcmp(cmd, "pane.copy_mode.yank") == 0) {
        lmux_workspace *ws = lmux_workspace_focused(app);
        if (!ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no workspaces\"}}");
            goto done;
        }
        lmux_pane *p = lmux_pane_focused(ws);
        if (!p) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no focused pane\"}}");
            goto done;
        }
        if (!p->copy_mode_active) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_state\",\"message\":\"copy mode not active\"}}");
            goto done;
        }
        /* Yank: copy selected text to clipboard.
         * Simplified: copy a placeholder based on selection positions. */
        char yank_buf[512];
        int n = snprintf(yank_buf, sizeof yank_buf,
            "[selection:(%d,%d)-(%d,%d)]",
            p->copy_sel_start_row, p->copy_sel_start_col,
            p->copy_cursor_row, p->copy_cursor_col);
        if (n > 0) lmux_clipboard_set(yank_buf, (size_t)n);
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"pane_id\":%u,\"yanked\":true,\"len\":%d}}",
            p->id, n);
        goto done;
    }

    /* --- pane.copy_mode.paste --- */
    if (strcmp(cmd, "pane.copy_mode.paste") == 0) {
        lmux_workspace *ws = lmux_workspace_focused(app);
        if (!ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no workspaces\"}}");
            goto done;
        }
        lmux_pane *p = lmux_pane_focused(ws);
        if (!p) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no focused pane\"}}");
            goto done;
        }
        if (!p->copy_mode_active) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_state\",\"message\":\"copy mode not active\"}}");
            goto done;
        }
        char paste_buf[65536];
        size_t paste_len = lmux_clipboard_get(paste_buf, sizeof paste_buf - 1);
        paste_buf[paste_len] = '\0';
        /* In a real terminal, we'd send paste_buf to the pty. Here we just
         * report what was pasted. */
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"pane_id\":%u,\"pasted\":true,\"len\":%zu}}",
            p->id, paste_len);
        goto done;
    }

    /* --- file_explorer.navigate --- */
    if (strcmp(cmd, "file_explorer.navigate") == 0) {
        if (!app->file_explorer.active) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_state\",\"message\":\"file explorer not open\"}}");
            goto done;
        }
        char path[1024] = {0};
        json_extract_string(args_json, "path", path, sizeof path);
        if (!path[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing path\"}}");
            goto done;
        }
        /* Validate path exists and is a directory */
        struct stat st;
        if (stat(path, &st) != 0 || !S_ISDIR(st.st_mode)) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"directory not found: %s\"}}",
                path);
            goto done;
        }
        file_explorer_scan_dir(&app->file_explorer, path);
        file_explorer_apply_sort(&app->file_explorer);
        app->file_explorer.active = true;
        app->file_explorer.selected_index = 0;
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"path\":\"%s\",\"count\":%d}}",
            JESC(app->file_explorer.current_path), app->file_explorer.entry_count);
        goto done;
    }

    /* --- file_explorer.list --- */
    if (strcmp(cmd, "file_explorer.list") == 0) {
        if (!app->file_explorer.active) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_state\",\"message\":\"file explorer not open\"}}");
            goto done;
        }
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"path\":\"%s\",\"entries\":[",
            JESC(app->file_explorer.current_path));
        int visible = 0;
        for (int i = 0; i < app->file_explorer.entry_count; i++) {
            lmux_file_entry *e = &app->file_explorer.entries[i];
            /* Apply filter */
            if (app->file_explorer.filter[0] &&
                !strcasestr(e->name, app->file_explorer.filter))
                continue;
            RESULT_GROW;
            char _ne[512], _pe[1024];
            if (visible > 0) written += snprintf(result + written, result_cap - written, ",");
            written += snprintf(result + written, result_cap - written,
                "{\"name\":\"%s\",\"path\":\"%s\",\"is_dir\":%s,\"size\":%lld,\"mtime\":%lld}",
                json_escape_str(_ne, sizeof _ne, e->name),
                json_escape_str(_pe, sizeof _pe, e->path),
                e->is_dir ? "true" : "false",
                (long long)e->size, (long long)e->mtime);
            visible++;
        }
        written += snprintf(result + written, result_cap - written,
            "],\"count\":%d,\"selected\":%d,\"filter\":\"%s\",\"sort_mode\":%d}}",
            visible, app->file_explorer.selected_index,
            JESC(app->file_explorer.filter), app->file_explorer.sort_mode);
        goto done;
    }

    /* --- file_explorer.open --- */
    if (strcmp(cmd, "file_explorer.open") == 0) {
        /* Open file explorer at a path, or current path if not specified */
        char path[1024] = {0};
        json_extract_string(args_json, "path", path, sizeof path);
        if (!path[0]) {
            /* Default to "/" or current working directory */
            if (app->ws_current && app->ws_current->cwd[0]) {
                snprintf(path, sizeof path, "%s", app->ws_current->cwd);
            } else {
                snprintf(path, sizeof path, "/");
            }
        }
        /* Validate path exists and is a directory */
        struct stat st;
        if (stat(path, &st) != 0 || !S_ISDIR(st.st_mode)) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"directory not found: %s\"}}",
                path);
            goto done;
        }
        file_explorer_scan_dir(&app->file_explorer, path);
        file_explorer_apply_sort(&app->file_explorer);
        app->file_explorer.active = true;
        app->file_explorer.selected_index = 0;
        app->file_explorer.filter[0] = '\0';
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"path\":\"%s\",\"count\":%d}}",
            JESC(app->file_explorer.current_path), app->file_explorer.entry_count);
        goto done;
    }

    /* --- file_explorer.refresh --- */
    if (strcmp(cmd, "file_explorer.refresh") == 0) {
        if (!app->file_explorer.active) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_state\",\"message\":\"file explorer not open\"}}");
            goto done;
        }
        /* Copy path before scan_dir overwrites current_path */
        char refresh_path[1024];
        snprintf(refresh_path, sizeof refresh_path, "%s", app->file_explorer.current_path);
        file_explorer_scan_dir(&app->file_explorer, refresh_path);
        file_explorer_apply_sort(&app->file_explorer);
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"path\":\"%s\",\"count\":%d}}",
            JESC(app->file_explorer.current_path), app->file_explorer.entry_count);
        goto done;
    }

    /* --- file_explorer.filter --- */
    if (strcmp(cmd, "file_explorer.filter") == 0) {
        if (!app->file_explorer.active) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_state\",\"message\":\"file explorer not open\"}}");
            goto done;
        }
        char filter[256] = {0};
        json_extract_string(args_json, "filter", filter, sizeof filter);
        snprintf(app->file_explorer.filter, sizeof app->file_explorer.filter, "%s", filter);
        app->file_explorer.selected_index = 0;
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"filter\":\"%s\",\"count\":%d}}",
            JESC(app->file_explorer.filter), file_explorer_entry_count(&app->file_explorer));
        goto done;
    }

    /* --- file_explorer.sort --- */
    if (strcmp(cmd, "file_explorer.sort") == 0) {
        if (!app->file_explorer.active) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_state\",\"message\":\"file explorer not open\"}}");
            goto done;
        }
        char mode[32] = {0};
        json_extract_string(args_json, "mode", mode, sizeof mode);
        if (strcmp(mode, "name") == 0) app->file_explorer.sort_mode = FILE_SORT_NAME;
        else if (strcmp(mode, "size") == 0) app->file_explorer.sort_mode = FILE_SORT_SIZE;
        else if (strcmp(mode, "time") == 0) app->file_explorer.sort_mode = FILE_SORT_TIME;
        else {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"invalid sort mode: %s\"}}",
                mode);
            goto done;
        }
        file_explorer_apply_sort(&app->file_explorer);
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"sort_mode\":%d}}", app->file_explorer.sort_mode);
        goto done;
    }

    /* --- file_explorer.open_file --- */
    if (strcmp(cmd, "file_explorer.open_file") == 0) {
        if (!app->file_explorer.active) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_state\",\"message\":\"file explorer not open\"}}");
            goto done;
        }
        char name[256] = {0};
        json_extract_string(args_json, "name", name, sizeof name);
        if (!name[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing name\"}}");
            goto done;
        }
        /* Find the entry */
        int idx = -1;
        for (int i = 0; i < app->file_explorer.entry_count; i++) {
            if (strcmp(app->file_explorer.entries[i].name, name) == 0) {
                idx = i;
                break;
            }
        }
        if (idx < 0) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"entry not found: %s\"}}",
                name);
            goto done;
        }
        lmux_file_entry *e = &app->file_explorer.entries[idx];
        if (e->is_dir) {
            /* Navigate into directory */
            file_explorer_scan_dir(&app->file_explorer, e->path);
            file_explorer_apply_sort(&app->file_explorer);
            app->file_explorer.selected_index = 0;
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"type\":\"directory\",\"path\":\"%s\",\"count\":%d}}",
                JESC(app->file_explorer.current_path), app->file_explorer.entry_count);
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"type\":\"file\",\"name\":\"%s\",\"path\":\"%s\",\"size\":%lld}}",
                JESC(e->name), JESC(e->path), (long long)e->size);
        }
        goto done;
    }

    /* --- file_explorer.create_dir --- */
    if (strcmp(cmd, "file_explorer.create_dir") == 0) {
        if (!app->file_explorer.active) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_state\",\"message\":\"file explorer not open\"}}");
            goto done;
        }
        char dirname[256] = {0};
        json_extract_string(args_json, "name", dirname, sizeof dirname);
        if (!dirname[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing name\"}}");
            goto done;
        }
        /* Security: reject path traversal in directory name */
        if (!is_safe_path(dirname, app->file_explorer.current_path)) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"directory name contains traversal sequences\"}}");
            goto done;
        }
        /* Build full path */
        char fullpath[1024];
        if (app->file_explorer.current_path[strlen(app->file_explorer.current_path) - 1] == '/')
            snprintf(fullpath, sizeof fullpath, "%s%s", app->file_explorer.current_path, dirname);
        else
            snprintf(fullpath, sizeof fullpath, "%s/%s", app->file_explorer.current_path, dirname);
        if (mkdir(fullpath, 0755) != 0) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"create_failed\",\"message\":\"failed to create directory: %s\"}}",
                dirname);
            goto done;
        }
        /* Refresh directory listing — copy path first since scan_dir overwrites current_path */
        {
            char _rp[1024];
            snprintf(_rp, sizeof _rp, "%s", app->file_explorer.current_path);
            file_explorer_scan_dir(&app->file_explorer, _rp);
        }
        file_explorer_apply_sort(&app->file_explorer);
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"name\":\"%s\",\"path\":\"%s\"}}",
            JESC(dirname), JESC(fullpath));
        goto done;
    }

    /* --- file_explorer.delete --- */
    if (strcmp(cmd, "file_explorer.delete") == 0) {
        if (!app->file_explorer.active) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_state\",\"message\":\"file explorer not open\"}}");
            goto done;
        }
        char name[256] = {0};
        json_extract_string(args_json, "name", name, sizeof name);
        if (!name[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing name\"}}");
            goto done;
        }
        /* Security: reject path traversal in file name */
        if (strchr(name, '/') || strstr(name, "..")) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"file name contains path separators\"}}");
            goto done;
        }
        /* Find the entry */
        int idx = -1;
        for (int i = 0; i < app->file_explorer.entry_count; i++) {
            if (strcmp(app->file_explorer.entries[i].name, name) == 0) {
                idx = i;
                break;
            }
        }
        if (idx < 0) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"entry not found: %s\"}}",
                name);
            goto done;
        }
        lmux_file_entry *e = &app->file_explorer.entries[idx];
        int ret;
        if (e->is_dir) {
            ret = rmdir(e->path);
        } else {
            ret = unlink(e->path);
        }
        if (ret != 0) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"delete_failed\",\"message\":\"failed to delete: %s\"}}",
                name);
            goto done;
        }
        /* Refresh directory listing — copy path first since scan_dir overwrites current_path */
        {
            char _rp[1024];
            snprintf(_rp, sizeof _rp, "%s", app->file_explorer.current_path);
            file_explorer_scan_dir(&app->file_explorer, _rp);
        }
        file_explorer_apply_sort(&app->file_explorer);
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"name\":\"%s\"}}", JESC(name));
        goto done;
    }

    /* --- file_explorer.rename --- */
    if (strcmp(cmd, "file_explorer.rename") == 0) {
        if (!app->file_explorer.active) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_state\",\"message\":\"file explorer not open\"}}");
            goto done;
        }
        char old_name[256] = {0}, new_name[256] = {0};
        json_extract_string(args_json, "old_name", old_name, sizeof old_name);
        json_extract_string(args_json, "new_name", new_name, sizeof new_name);
        if (!old_name[0] || !new_name[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing old_name or new_name\"}}");
            goto done;
        }
        /* Security: reject path traversal in file names */
        if (strchr(old_name, '/') || strstr(old_name, "..") ||
            strchr(new_name, '/') || strstr(new_name, "..")) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"file name contains path separators\"}}");
            goto done;
        }
        /* Find the entry */
        int idx = -1;
        for (int i = 0; i < app->file_explorer.entry_count; i++) {
            if (strcmp(app->file_explorer.entries[i].name, old_name) == 0) {
                idx = i;
                break;
            }
        }
        if (idx < 0) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"entry not found: %s\"}}",
                old_name);
            goto done;
        }
        lmux_file_entry *e = &app->file_explorer.entries[idx];
        /* Build new path */
        char newpath[1024];
        if (app->file_explorer.current_path[strlen(app->file_explorer.current_path) - 1] == '/')
            snprintf(newpath, sizeof newpath, "%s%s", app->file_explorer.current_path, new_name);
        else
            snprintf(newpath, sizeof newpath, "%s/%s", app->file_explorer.current_path, new_name);
        if (rename(e->path, newpath) != 0) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"rename_failed\",\"message\":\"failed to rename: %s\"}}",
                old_name);
            goto done;
        }
        /* Refresh directory listing — copy path first since scan_dir overwrites current_path */
        {
            char _rp[1024];
            snprintf(_rp, sizeof _rp, "%s", app->file_explorer.current_path);
            file_explorer_scan_dir(&app->file_explorer, _rp);
        }
        file_explorer_apply_sort(&app->file_explorer);
        {
            char _old[256], _new[256];
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"old_name\":\"%s\",\"new_name\":\"%s\"}}",
                json_escape_str(_old, sizeof _old, old_name),
                json_escape_str(_new, sizeof _new, new_name));
        }
        goto done;
    }

    /* --- file_explorer.search --- */
    if (strcmp(cmd, "file_explorer.search") == 0) {
        if (!app->file_explorer.active) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_state\",\"message\":\"file explorer not open\"}}");
            goto done;
        }
        char query[256] = {0};
        json_extract_string(args_json, "query", query, sizeof query);
        if (!query[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing query\"}}");
            goto done;
        }
        /* Search entries matching query */
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"query\":\"%s\",\"matches\":[", JESC(query));
        int match_count = 0;
        for (int i = 0; i < app->file_explorer.entry_count; i++) {
            lmux_file_entry *e = &app->file_explorer.entries[i];
            if (strcasestr(e->name, query)) {
                RESULT_GROW;
                char _ne[512], _pe[1024];
                if (match_count > 0) written += snprintf(result + written, result_cap - written, ",");
                written += snprintf(result + written, result_cap - written,
                    "{\"name\":\"%s\",\"path\":\"%s\",\"is_dir\":%s}",
                    json_escape_str(_ne, sizeof _ne, e->name),
                    json_escape_str(_pe, sizeof _pe, e->path),
                    e->is_dir ? "true" : "false");
                match_count++;
            }
        }
        written += snprintf(result + written, result_cap - written, "],\"count\":%d}}", match_count);
        goto done;
    }

    /* --- file_explorer.close --- */
    if (strcmp(cmd, "file_explorer.close") == 0) {
        app->file_explorer.active = false;
        app->file_explorer.entry_count = 0;
        app->file_explorer.filter[0] = '\0';
        written = snprintf(result, result_cap, "{\"ok\":true,\"result\":{}}");
        goto done;
    }

    /* --- surface.canvas.enable --- */
    if (strcmp(cmd, "surface.canvas.enable") == 0) {
        /* Get surface ID from args, or use focused surface */
        char s_id_str[64] = {0};
        json_extract_string(args_json, "surface_id", s_id_str, sizeof s_id_str);
        lmux_id surface_id = s_id_str[0] ? (lmux_id)atol(s_id_str) : 0;
        lmux_surface *surf = NULL;
        if (surface_id) {
            /* Find surface by ID */
            for (size_t wi = 0; wi < app->workspaces.len; wi++) {
                lmux_workspace *ws = (lmux_workspace *)app->workspaces.items[wi];
                for (size_t si = 0; si < ws->surfaces.len; si++) {
                    lmux_surface *s = (lmux_surface *)ws->surfaces.items[si];
                    if (s->id == surface_id) {
                        surf = s;
                        break;
                    }
                }
                if (surf) break;
            }
        } else {
            /* Use focused surface */
            for (size_t wi = 0; wi < app->workspaces.len; wi++) {
                lmux_workspace *ws = (lmux_workspace *)app->workspaces.items[wi];
                for (size_t si = 0; si < ws->surfaces.len; si++) {
                    lmux_surface *s = (lmux_surface *)ws->surfaces.items[si];
                    if (s->focused) {
                        surf = s;
                        break;
                    }
                }
                if (surf) break;
            }
        }
        if (!surf) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"surface not found\"}}");
            goto done;
        }
        surf->canvas_mode = true;
        /* Initialize canvas_pane_count to current pane count if not set */
        if (surf->canvas_pane_count == 0) {
            surf->canvas_pane_count = (int)surf->panes.len;
            /* Default positions: grid layout */
            for (int i = 0; i < surf->canvas_pane_count && i < 64; i++) {
                int cols = 2;
                int col = i % cols;
                int row = i / cols;
                surf->canvas_pane_x[i] = col * 480;
                surf->canvas_pane_y[i] = row * 270;
                surf->canvas_pane_w[i] = 480;
                surf->canvas_pane_h[i] = 270;
                surf->canvas_pane_z[i] = i;
            }
        }
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"canvas_mode\":true}}");
        goto done;
    }

    /* --- surface.canvas.disable --- */
    if (strcmp(cmd, "surface.canvas.disable") == 0) {
        char s_id_str[64] = {0};
        json_extract_string(args_json, "surface_id", s_id_str, sizeof s_id_str);
        lmux_id surface_id = s_id_str[0] ? (lmux_id)atol(s_id_str) : 0;
        lmux_surface *surf = NULL;
        if (surface_id) {
            for (size_t wi = 0; wi < app->workspaces.len; wi++) {
                lmux_workspace *ws = (lmux_workspace *)app->workspaces.items[wi];
                for (size_t si = 0; si < ws->surfaces.len; si++) {
                    lmux_surface *s = (lmux_surface *)ws->surfaces.items[si];
                    if (s->id == surface_id) {
                        surf = s;
                        break;
                    }
                }
                if (surf) break;
            }
        } else {
            for (size_t wi = 0; wi < app->workspaces.len; wi++) {
                lmux_workspace *ws = (lmux_workspace *)app->workspaces.items[wi];
                for (size_t si = 0; si < ws->surfaces.len; si++) {
                    lmux_surface *s = (lmux_surface *)ws->surfaces.items[si];
                    if (s->focused) {
                        surf = s;
                        break;
                    }
                }
                if (surf) break;
            }
        }
        if (!surf) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"surface not found\"}}");
            goto done;
        }
        surf->canvas_mode = false;
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"canvas_mode\":false}}");
        goto done;
    }

    /* --- surface.canvas.move_pane --- */
    if (strcmp(cmd, "surface.canvas.move_pane") == 0) {
        char s_id_str[64] = {0};
        char pane_idx_str[64] = {0};
        char x_str[64] = {0};
        char y_str[64] = {0};
        json_extract_string(args_json, "surface_id", s_id_str, sizeof s_id_str);
        json_extract_string(args_json, "pane_index", pane_idx_str, sizeof pane_idx_str);
        json_extract_string(args_json, "x", x_str, sizeof x_str);
        json_extract_string(args_json, "y", y_str, sizeof y_str);
        lmux_id surface_id = s_id_str[0] ? (lmux_id)atol(s_id_str) : 0;
        int pane_index = pane_idx_str[0] ? atoi(pane_idx_str) : -1;
        int x = x_str[0] ? atoi(x_str) : 0;
        int y = y_str[0] ? atoi(y_str) : 0;
        lmux_surface *surf = NULL;
        if (surface_id) {
            for (size_t wi = 0; wi < app->workspaces.len; wi++) {
                lmux_workspace *ws = (lmux_workspace *)app->workspaces.items[wi];
                for (size_t si = 0; si < ws->surfaces.len; si++) {
                    lmux_surface *s = (lmux_surface *)ws->surfaces.items[si];
                    if (s->id == surface_id) {
                        surf = s;
                        break;
                    }
                }
                if (surf) break;
            }
        } else {
            for (size_t wi = 0; wi < app->workspaces.len; wi++) {
                lmux_workspace *ws = (lmux_workspace *)app->workspaces.items[wi];
                for (size_t si = 0; si < ws->surfaces.len; si++) {
                    lmux_surface *s = (lmux_surface *)ws->surfaces.items[si];
                    if (s->focused) {
                        surf = s;
                        break;
                    }
                }
                if (surf) break;
            }
        }
        if (!surf) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"surface not found\"}}");
            goto done;
        }
        if (!surf->canvas_mode) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_state\",\"message\":\"canvas mode not enabled\"}}");
            goto done;
        }
        if (pane_index < 0 || pane_index >= 64 || pane_index >= surf->canvas_pane_count) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"invalid pane_index\"}}");
            goto done;
        }
        surf->canvas_pane_x[pane_index] = x;
        surf->canvas_pane_y[pane_index] = y;
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"pane_index\":%d,\"x\":%d,\"y\":%d}}",
            pane_index, x, y);
        goto done;
    }

    /* --- surface.canvas.resize_pane --- */
    if (strcmp(cmd, "surface.canvas.resize_pane") == 0) {
        char s_id_str[64] = {0};
        char pane_idx_str[64] = {0};
        char w_str[64] = {0};
        char h_str[64] = {0};
        json_extract_string(args_json, "surface_id", s_id_str, sizeof s_id_str);
        json_extract_string(args_json, "pane_index", pane_idx_str, sizeof pane_idx_str);
        json_extract_string(args_json, "w", w_str, sizeof w_str);
        json_extract_string(args_json, "h", h_str, sizeof h_str);
        lmux_id surface_id = s_id_str[0] ? (lmux_id)atol(s_id_str) : 0;
        int pane_index = pane_idx_str[0] ? atoi(pane_idx_str) : -1;
        int w = w_str[0] ? atoi(w_str) : 0;
        int h = h_str[0] ? atoi(h_str) : 0;
        lmux_surface *surf = NULL;
        if (surface_id) {
            for (size_t wi = 0; wi < app->workspaces.len; wi++) {
                lmux_workspace *ws = (lmux_workspace *)app->workspaces.items[wi];
                for (size_t si = 0; si < ws->surfaces.len; si++) {
                    lmux_surface *s = (lmux_surface *)ws->surfaces.items[si];
                    if (s->id == surface_id) {
                        surf = s;
                        break;
                    }
                }
                if (surf) break;
            }
        } else {
            for (size_t wi = 0; wi < app->workspaces.len; wi++) {
                lmux_workspace *ws = (lmux_workspace *)app->workspaces.items[wi];
                for (size_t si = 0; si < ws->surfaces.len; si++) {
                    lmux_surface *s = (lmux_surface *)ws->surfaces.items[si];
                    if (s->focused) {
                        surf = s;
                        break;
                    }
                }
                if (surf) break;
            }
        }
        if (!surf) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"surface not found\"}}");
            goto done;
        }
        if (!surf->canvas_mode) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_state\",\"message\":\"canvas mode not enabled\"}}");
            goto done;
        }
        if (pane_index < 0 || pane_index >= 64 || pane_index >= surf->canvas_pane_count) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"invalid pane_index\"}}");
            goto done;
        }
        if (w < 1 || h < 1) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"w and h must be positive\"}}");
            goto done;
        }
        surf->canvas_pane_w[pane_index] = w;
        surf->canvas_pane_h[pane_index] = h;
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"pane_index\":%d,\"w\":%d,\"h\":%d}}",
            pane_index, w, h);
        goto done;
    }

    /* --- surface.canvas.set_z --- */
    if (strcmp(cmd, "surface.canvas.set_z") == 0) {
        char s_id_str[64] = {0};
        char pane_idx_str[64] = {0};
        char z_str[64] = {0};
        json_extract_string(args_json, "surface_id", s_id_str, sizeof s_id_str);
        json_extract_string(args_json, "pane_index", pane_idx_str, sizeof pane_idx_str);
        json_extract_string(args_json, "z", z_str, sizeof z_str);
        lmux_id surface_id = s_id_str[0] ? (lmux_id)atol(s_id_str) : 0;
        int pane_index = pane_idx_str[0] ? atoi(pane_idx_str) : -1;
        int z = z_str[0] ? atoi(z_str) : 0;
        lmux_surface *surf = NULL;
        if (surface_id) {
            for (size_t wi = 0; wi < app->workspaces.len; wi++) {
                lmux_workspace *ws = (lmux_workspace *)app->workspaces.items[wi];
                for (size_t si = 0; si < ws->surfaces.len; si++) {
                    lmux_surface *s = (lmux_surface *)ws->surfaces.items[si];
                    if (s->id == surface_id) {
                        surf = s;
                        break;
                    }
                }
                if (surf) break;
            }
        } else {
            for (size_t wi = 0; wi < app->workspaces.len; wi++) {
                lmux_workspace *ws = (lmux_workspace *)app->workspaces.items[wi];
                for (size_t si = 0; si < ws->surfaces.len; si++) {
                    lmux_surface *s = (lmux_surface *)ws->surfaces.items[si];
                    if (s->focused) {
                        surf = s;
                        break;
                    }
                }
                if (surf) break;
            }
        }
        if (!surf) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"surface not found\"}}");
            goto done;
        }
        if (!surf->canvas_mode) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_state\",\"message\":\"canvas mode not enabled\"}}");
            goto done;
        }
        if (pane_index < 0 || pane_index >= 64 || pane_index >= surf->canvas_pane_count) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"invalid pane_index\"}}");
            goto done;
        }
        surf->canvas_pane_z[pane_index] = z;
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"pane_index\":%d,\"z\":%d}}",
            pane_index, z);
        goto done;
    }

    /* --- surface.canvas.get_layout --- */
    if (strcmp(cmd, "surface.canvas.get_layout") == 0) {
        char s_id_str[64] = {0};
        json_extract_string(args_json, "surface_id", s_id_str, sizeof s_id_str);
        lmux_id surface_id = s_id_str[0] ? (lmux_id)atol(s_id_str) : 0;
        lmux_surface *surf = NULL;
        if (surface_id) {
            for (size_t wi = 0; wi < app->workspaces.len; wi++) {
                lmux_workspace *ws = (lmux_workspace *)app->workspaces.items[wi];
                for (size_t si = 0; si < ws->surfaces.len; si++) {
                    lmux_surface *s = (lmux_surface *)ws->surfaces.items[si];
                    if (s->id == surface_id) {
                        surf = s;
                        break;
                    }
                }
                if (surf) break;
            }
        } else {
            for (size_t wi = 0; wi < app->workspaces.len; wi++) {
                lmux_workspace *ws = (lmux_workspace *)app->workspaces.items[wi];
                for (size_t si = 0; si < ws->surfaces.len; si++) {
                    lmux_surface *s = (lmux_surface *)ws->surfaces.items[si];
                    if (s->focused) {
                        surf = s;
                        break;
                    }
                }
                if (surf) break;
            }
        }
        if (!surf) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"surface not found\"}}");
            goto done;
        }
        /* Build layout JSON */
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"canvas_mode\":%s,\"pane_count\":%d,\"panes\":[",
            surf->canvas_mode ? "true" : "false",
            surf->canvas_pane_count);
        for (int i = 0; i < surf->canvas_pane_count && i < 64; i++) {
            if (i > 0) written += snprintf(result + written, result_cap - written, ",");
            written += snprintf(result + written, result_cap - written,
                "{\"index\":%d,\"x\":%d,\"y\":%d,\"w\":%d,\"h\":%d,\"z\":%d}",
                i,
                surf->canvas_pane_x[i],
                surf->canvas_pane_y[i],
                surf->canvas_pane_w[i],
                surf->canvas_pane_h[i],
                surf->canvas_pane_z[i]);
        }
        written += snprintf(result + written, result_cap - written, "]}}");
        goto done;
    }

    /* --- surface.canvas.set_layout --- */
    if (strcmp(cmd, "surface.canvas.set_layout") == 0) {
        char s_id_str[64] = {0};
        json_extract_string(args_json, "surface_id", s_id_str, sizeof s_id_str);
        lmux_id surface_id = s_id_str[0] ? (lmux_id)atol(s_id_str) : 0;
        lmux_surface *surf = NULL;
        if (surface_id) {
            for (size_t wi = 0; wi < app->workspaces.len; wi++) {
                lmux_workspace *ws = (lmux_workspace *)app->workspaces.items[wi];
                for (size_t si = 0; si < ws->surfaces.len; si++) {
                    lmux_surface *s = (lmux_surface *)ws->surfaces.items[si];
                    if (s->id == surface_id) {
                        surf = s;
                        break;
                    }
                }
                if (surf) break;
            }
        } else {
            for (size_t wi = 0; wi < app->workspaces.len; wi++) {
                lmux_workspace *ws = (lmux_workspace *)app->workspaces.items[wi];
                for (size_t si = 0; si < ws->surfaces.len; si++) {
                    lmux_surface *s = (lmux_surface *)ws->surfaces.items[si];
                    if (s->focused) {
                        surf = s;
                        break;
                    }
                }
                if (surf) break;
            }
        }
        if (!surf) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"surface not found\"}}");
            goto done;
        }
        /* Parse panes array from args */
        const char *panes_arr = json_find_key(args_json, "panes");
        if (!panes_arr) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing panes array\"}}");
            goto done;
        }
        /* Find the opening bracket */
        const char *p = strchr(panes_arr, '[');
        if (!p) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"invalid panes array\"}}");
            goto done;
        }
        p++;
        int count = 0;
        while (*p && count < 64) {
            /* Skip whitespace */
            while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') p++;
            if (*p != '{') break;
            /* Find matching brace */
            const char *obj_start = p;
            int depth = 1;
            p++;
            while (*p && depth > 0) {
                if (*p == '{') depth++;
                if (*p == '}') depth--;
                p++;
            }
            /* Parse object */
            char obj_buf[512];
            size_t obj_len = (size_t)(p - obj_start);
            if (obj_len >= sizeof obj_buf) obj_len = sizeof obj_buf - 1;
            memcpy(obj_buf, obj_start, obj_len);
            obj_buf[obj_len] = 0;
            
            char x_str[64] = {0}, y_str[64] = {0}, w_str[64] = {0}, h_str[64] = {0}, z_str[64] = {0};
            json_extract_string(obj_buf, "x", x_str, sizeof x_str);
            json_extract_string(obj_buf, "y", y_str, sizeof y_str);
            json_extract_string(obj_buf, "w", w_str, sizeof w_str);
            json_extract_string(obj_buf, "h", h_str, sizeof h_str);
            json_extract_string(obj_buf, "z", z_str, sizeof z_str);
            
            int x = x_str[0] ? atoi(x_str) : 0;
            int y = y_str[0] ? atoi(y_str) : 0;
            int w = w_str[0] ? atoi(w_str) : 480;
            int h = h_str[0] ? atoi(h_str) : 270;
            int z = z_str[0] ? atoi(z_str) : count;
            
            surf->canvas_pane_x[count] = x;
            surf->canvas_pane_y[count] = y;
            surf->canvas_pane_w[count] = w;
            surf->canvas_pane_h[count] = h;
            surf->canvas_pane_z[count] = z;
            count++;
            
            /* Skip comma between objects */
            while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') p++;
            if (*p == ',') p++;
        }
        surf->canvas_pane_count = count;
        surf->canvas_mode = true;
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"pane_count\":%d}}", count);
        goto done;
    }

    /* --- search.start --- */
    if (strcmp(cmd, "search.start") == 0) {
        char query[256] = {0};
        json_extract_string(args_json, "query", query, sizeof query);
        bool case_sensitive = false;
        json_extract_bool(args_json, "case_sensitive", &case_sensitive);
        bool whole_words = false;
        json_extract_bool(args_json, "whole_words", &whole_words);
        bool regex = false;
        json_extract_bool(args_json, "regex", &regex);
        char scope[16] = "current";
        json_extract_string(args_json, "scope", scope, sizeof scope);

        if (!query[0]) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_params\",\"message\":\"missing query\"}}");
            goto done;
        }

        lmux_workspace *ws = lmux_workspace_focused(app);
        if (!ws) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no workspaces\"}}");
            goto done;
        }
        lmux_pane *p = lmux_pane_focused(ws);
        if (!p) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"not_found\",\"message\":\"no focused pane\"}}");
            goto done;
        }

        /* Activate search */
        app->search_active = true;
        snprintf(app->search_query, sizeof app->search_query, "%s", query);
        app->search_case_sensitive = case_sensitive;
        app->search_whole_words = whole_words;
        app->search_regex = regex;
        snprintf(app->search_scope, sizeof app->search_scope, "%s", scope);
        app->search_pane = p;
        app->search_match_count = 0;  /* simplified: no actual content search */
        app->search_current_match = 0;

        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"pane_id\":%u,\"query\":\"%s\",\"active\":true,"
            "\"case_sensitive\":%s,\"whole_words\":%s,\"regex\":%s,\"scope\":\"%s\"}}",
            p->id, JESC(query),
            case_sensitive ? "true" : "false",
            whole_words ? "true" : "false",
            regex ? "true" : "false",
            scope);
        goto done;
    }

    /* --- search.next --- */
    if (strcmp(cmd, "search.next") == 0) {
        if (!app->search_active) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_state\",\"message\":\"no active search\"}}");
            goto done;
        }
        /* Move to next match (simplified: cycle through matches) */
        if (app->search_match_count > 0) {
            app->search_current_match = (app->search_current_match + 1) % app->search_match_count;
        }
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"match_index\":%d,\"match_count\":%d}}",
            app->search_current_match, app->search_match_count);
        goto done;
    }

    /* --- search.prev --- */
    if (strcmp(cmd, "search.prev") == 0) {
        if (!app->search_active) {
            written = snprintf(result, result_cap,
                "{\"ok\":false,\"error\":{\"code\":\"invalid_state\",\"message\":\"no active search\"}}");
            goto done;
        }
        /* Move to previous match (simplified: cycle through matches) */
        if (app->search_match_count > 0) {
            app->search_current_match--;
            if (app->search_current_match < 0)
                app->search_current_match = app->search_match_count - 1;
        }
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"match_index\":%d,\"match_count\":%d}}",
            app->search_current_match, app->search_match_count);
        goto done;
    }

    /* --- search.cancel --- */
    if (strcmp(cmd, "search.cancel") == 0) {
        bool was_active = app->search_active;
        app->search_active = false;
        app->search_query[0] = '\0';
        app->search_match_count = 0;
        app->search_current_match = 0;
        app->search_pane = NULL;
        written = snprintf(result, result_cap,
            "{\"ok\":true,\"result\":{\"active\":false,\"was_active\":%s}}",
            was_active ? "true" : "false");
        goto done;
    }

    /* --- search.status --- */
    if (strcmp(cmd, "search.status") == 0) {
        if (app->search_active) {
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"active\":true,\"query\":\"%s\","
                "\"match_count\":%d,\"current_match\":%d,"
                "\"case_sensitive\":%s,\"whole_words\":%s,\"regex\":%s,\"scope\":\"%s\"}}",
                JESC(app->search_query),
                app->search_match_count, app->search_current_match,
                app->search_case_sensitive ? "true" : "false",
                app->search_whole_words ? "true" : "false",
                app->search_regex ? "true" : "false",
                app->search_scope);
        } else {
            written = snprintf(result, result_cap,
                "{\"ok\":true,\"result\":{\"active\":false}}");
        }
        goto done;
    }

    /* --- unknown command --- */
    written = snprintf(result, result_cap,
        "{\"ok\":false,\"error\":{\"code\":\"unknown_command\",\"message\":\"unknown command: %s\"}}",
        cmd);

done:
    return result;
}

static bool is_readonly_cmd(const char *cmd) {
    /* Read-only commands: queries that don't mutate model state. */
    static const char *ro[] = {
        "ping", "workspace.list", "workspace.current", "workspace.focused",
        "workspace.count", "workspace.get_info",
        "surface.list", "surface.focused", "surface.count",
        "pane.list", "pane.focused", "pane.count", "pane.get_info",
        "pane.copy_mode.enter", "pane.copy_mode.exit", "pane.copy_mode.move",
        "pane.copy_mode.select_start", "pane.copy_mode.select_end",
        "pane.copy_mode.yank", "pane.copy_mode.paste",
        "tree", "notification.list", "notification.count",
        "agent.list", "agent.get", "config.get",
        "events", "version",
        "file_explorer.list", "file_explorer.search",
        "surface.canvas.get_layout",
        "search.status",
    };
    for (size_t i = 0; i < sizeof ro / sizeof ro[0]; i++) {
        if (strcmp(cmd, ro[i]) == 0) return true;
    }
    return false;
}

/* ------------------------------------------------------------------ */
/* Canvas layout persistence                                           */
/* ------------------------------------------------------------------ */

bool canvas_layout_save(const lmux_surface *surf, const char *path) {
    if (!surf || !path) return false;
    FILE *f = fopen(path, "w");
    if (!f) return false;
    fprintf(f, "{\"canvas_mode\":%s,\"pane_count\":%d,\"panes\":[",
        surf->canvas_mode ? "true" : "false",
        surf->canvas_pane_count);
    for (int i = 0; i < surf->canvas_pane_count && i < 64; i++) {
        if (i > 0) fprintf(f, ",");
        fprintf(f, "{\"x\":%d,\"y\":%d,\"w\":%d,\"h\":%d,\"z\":%d}",
            surf->canvas_pane_x[i],
            surf->canvas_pane_y[i],
            surf->canvas_pane_w[i],
            surf->canvas_pane_h[i],
            surf->canvas_pane_z[i]);
    }
    fprintf(f, "]}\n");
    fflush(f);
    fclose(f);
    return true;
}

bool canvas_layout_load(lmux_surface *surf, const char *path) {
    if (!surf || !path) return false;
    FILE *f = fopen(path, "r");
    if (!f) return false;
    char buf[16384];
    size_t n = fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    if (n == 0) return false;
    buf[n] = 0;

    /* Parse canvas_mode */
    const char *cm = strstr(buf, "\"canvas_mode\":");
    if (cm) {
        cm += 14;
        surf->canvas_mode = (strncmp(cm, "true", 4) == 0);
    }

    /* Parse pane_count */
    const char *pc = strstr(buf, "\"pane_count\":");
    if (pc) {
        pc += 13;
        surf->canvas_pane_count = atoi(pc);
        if (surf->canvas_pane_count < 0) surf->canvas_pane_count = 0;
        if (surf->canvas_pane_count > 64) surf->canvas_pane_count = 64;
    }

    /* Parse panes array */
    const char *panes = strstr(buf, "\"panes\":[");
    if (panes) {
        panes = strchr(panes, '[');
        if (panes) panes++;
        int count = 0;
        while (*panes && count < surf->canvas_pane_count && count < 64) {
            /* Skip whitespace */
            while (*panes == ' ' || *panes == '\t' || *panes == '\n' || *panes == '\r') panes++;
            if (*panes != '{') break;
            /* Find matching brace */
            const char *obj_start = panes;
            int depth = 1;
            panes++;
            while (*panes && depth > 0) {
                if (*panes == '{') depth++;
                if (*panes == '}') depth--;
                panes++;
            }
            /* Parse object */
            char obj_buf[256];
            size_t obj_len = (size_t)(panes - obj_start);
            if (obj_len >= sizeof obj_buf) obj_len = sizeof obj_buf - 1;
            memcpy(obj_buf, obj_start, obj_len);
            obj_buf[obj_len] = 0;

            char x_str[64] = {0}, y_str[64] = {0}, w_str[64] = {0}, h_str[64] = {0}, z_str[64] = {0};
            json_extract_string(obj_buf, "x", x_str, sizeof x_str);
            json_extract_string(obj_buf, "y", y_str, sizeof y_str);
            json_extract_string(obj_buf, "w", w_str, sizeof w_str);
            json_extract_string(obj_buf, "h", h_str, sizeof h_str);
            json_extract_string(obj_buf, "z", z_str, sizeof z_str);
            int x = x_str[0] ? atoi(x_str) : 0;
            int y = y_str[0] ? atoi(y_str) : 0;
            int w = w_str[0] ? atoi(w_str) : 480;
            int h = h_str[0] ? atoi(h_str) : 270;
            int z = z_str[0] ? atoi(z_str) : count;

            surf->canvas_pane_x[count] = x;
            surf->canvas_pane_y[count] = y;
            surf->canvas_pane_w[count] = w;
            surf->canvas_pane_h[count] = h;
            surf->canvas_pane_z[count] = z;
            count++;

            /* Skip comma between objects */
            while (*panes == ' ' || *panes == '\t' || *panes == '\n' || *panes == '\r') panes++;
            if (*panes == ',') panes++;
        }
    }
    return true;
}

/* ------------------------------------------------------------------ */
/* RFC 7807 Problem Details error response builder                      */
/* ------------------------------------------------------------------ */
/**
 * Build an RFC 7807-style JSON error response.
 * Returns a malloc'd string. Caller must free.
 *
 * Format: {"ok":false,"error":{"type":"...","title":"...","code":"...","detail":"...","trace_id":"..."}}
 */
static char *make_error_response(const char *type, const char *title,
                                  const char *code, const char *detail,
                                  const char *trace_id) {
    char *resp = malloc(1024);
    if (!resp) return strdup("{\"ok\":false,\"error\":{\"code\":\"oom\",\"title\":\"Out of Memory\"}}");
    snprintf(resp, 1024,
        "{\"ok\":false,\"error\":{\"type\":\"%s\",\"title\":\"%s\",\"code\":\"%s\",\"detail\":\"%s\",\"trace_id\":\"%s\"}}",
        type ? type : "about:blank",
        title ? title : "Error",
        code ? code : "unknown",
        detail ? detail : "",
        trace_id ? trace_id : "null");
    return resp;
}

char *lmux_dispatch_json(lmux_app *app, const char *json_request) {
    if (!app || !json_request) {
        return make_error_response("urn:lmux:invalid-request", "Invalid Request",
                                    "invalid_request", "null request", NULL);
    }

    /* Enforce maximum request size (64KB). */
    size_t req_len = strlen(json_request);
    if (req_len > 65536) {
        return make_error_response("urn:lmux:invalid-request", "Invalid Request",
                                    "invalid_request", "request too large (max 64KB)", NULL);
    }

    char cmd[128] = {0};
    json_extract_string(json_request, "cmd", cmd, sizeof cmd);

    if (!cmd[0]) {
        return make_error_response("urn:lmux:invalid-request", "Invalid Request",
                                    "invalid_request", "missing cmd field", NULL);
    }

    /* Validate command name: alphanumeric, dots, underscores, hyphens only. */
    for (const char *p = cmd; *p; p++) {
        char c = *p;
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-')) {
            return make_error_response("urn:lmux:invalid-request", "Invalid Request",
                                        "invalid_request", "invalid cmd characters", NULL);
        }
    }

    /* Extract args object (everything after "args":{) */
    const char *args_start = json_find_key(json_request, "args");
    /* Dynamic args buffer: starts at 1KB, grows if needed (L5 fix) */
    size_t args_cap = 1024;
    char *args_buf = calloc(args_cap, 1);
    if (!args_buf) {
        return make_error_response("urn:lmux:internal-error", "Internal Error",
                                    "oom", "out of memory", NULL);
    }
    if (args_start) {
        /* Find matching brace, skipping braces inside JSON strings.
         * Enforce max nesting depth of 8 to prevent stack abuse. */
        #define MAX_JSON_DEPTH 8
        int depth = 0;
        int in_string = 0;
        size_t j = 0;
        for (const char *p = args_start; *p && j < args_cap - 1; p++) {
            if (j + 2 >= args_cap) {
                /* Grow buffer (L5 fix) */
                args_cap *= 2;
                char *tmp = realloc(args_buf, args_cap);
                if (!tmp) { free(args_buf); return make_error_response("urn:lmux:internal-error", "Internal Error", "oom", "out of memory", NULL); }
                args_buf = tmp;
            }
            if (in_string) {
                args_buf[j++] = *p;
                if (*p == '\\' && *(p + 1)) {
                    /* skip escaped character */
                    p++;
                    if (*p && j < args_cap - 1) args_buf[j++] = *p;
                } else if (*p == '"') {
                    in_string = 0;
                }
            } else {
                if (*p == '"') {
                    in_string = 1;
                } else if (*p == '{') {
                    depth++;
                    if (depth > MAX_JSON_DEPTH) {
                        free(args_buf);
                        return make_error_response("urn:lmux:invalid-request", "Invalid Request",
                                                    "invalid_request", "JSON nesting too deep (max 8)", NULL);
                    }
                } else if (*p == '}') {
                    depth--;
                    if (depth < 0) break;
                }
                args_buf[j++] = *p;
                if (depth == 0) break;
            }
        }
        args_buf[j] = 0;
        #undef MAX_JSON_DEPTH
    }

    /* Use read-write lock: reads don't block concurrent reads (C1 fix). */
    char *response;
    if (is_readonly_cmd(cmd)) {
        pthread_rwlock_rdlock(&app->rw_lock);
        response = dispatch_command(app, cmd, args_buf);
        pthread_rwlock_unlock(&app->rw_lock);
    } else {
        pthread_rwlock_wrlock(&app->rw_lock);
        response = dispatch_command(app, cmd, args_buf);
        pthread_rwlock_unlock(&app->rw_lock);
    }
    free(args_buf);
    return response;
}

/* ------------------------------------------------------------------ */
/* Session auto-save / auto-restore                                     */
/* ------------------------------------------------------------------ */

void lmux_app_auto_save(lmux_app *app) {
    if (!app || !app->auto_save_path[0]) return;
    /* Ensure directory exists */
    char dirbuf[512];
    snprintf(dirbuf, sizeof dirbuf, "%s", app->auto_save_path);
    char *slash = strrchr(dirbuf, '/');
    if (slash) { *slash = 0; mkdir(dirbuf, 0755); }
    lmux_snapshot_save(app, app->auto_save_path);
    lmux_log(LMUX_LOG_DEBUG, "session: auto-saved to %s", app->auto_save_path);
}

void lmux_app_auto_restore(lmux_app *app) {
    if (!app || !app->auto_save_path[0]) return;
    struct stat st;
    if (stat(app->auto_save_path, &st) == 0) {
        lmux_snapshot_load(app, app->auto_save_path);
        lmux_log(LMUX_LOG_INFO, "session: restored from %s", app->auto_save_path);
    }
}
/* ------------------------------------------------------------------ */
/* Snapshot persistence                                                */
/* ------------------------------------------------------------------ */

bool lmux_snapshot_save(const lmux_app *app, const char *path) {
    if (!app || !path) return false;
    /* Write to a temp file first for atomicity. */
    char tmp_path[1024];
    snprintf(tmp_path, sizeof tmp_path, "%s.tmp", path);
    FILE *f = fopen(tmp_path, "w");
    if (!f) return false;
    fprintf(f, "{\"version\":1,\"workspaces\":[\n");
    for (size_t i = 0; i < app->workspaces.len; i++) {
        const lmux_workspace *ws = app->workspaces.items[i];
        if (i > 0) fprintf(f, ",\n");
        char _te[512], _ce[1024], _be[256];
        fprintf(f, "{\"id\":%u,\"title\":\"%s\",\"cwd\":\"%s\",\"git_branch\":\"%s\",\"surfaces\":[\n",
                ws->id,
                json_escape_str(_te, sizeof _te, ws->title),
                json_escape_str(_ce, sizeof _ce, ws->cwd),
                json_escape_str(_be, sizeof _be, ws->git_branch));
        for (size_t j = 0; j < ws->surfaces.len; j++) {
            const lmux_surface *s = ws->surfaces.items[j];
            if (j > 0) fprintf(f, ",\n");
            char _se[512];
            fprintf(f, "{\"id\":%u,\"title\":\"%s\",\"panes\":[\n",
                    s->id, json_escape_str(_se, sizeof _se, s->title));
            for (size_t k = 0; k < s->panes.len; k++) {
                const lmux_pane *p = s->panes.items[k];
                if (k > 0) fprintf(f, ",\n");
                char _ke[64], _cmd[1024];
                fprintf(f, "{\"id\":%u,\"kind\":\"%s\",\"command\":\"%s\"}",
                        p->id,
                        json_escape_str(_ke, sizeof _ke, p->kind),
                        json_escape_str(_cmd, sizeof _cmd, p->command));
            }
            fprintf(f, "]}");
        }
        fprintf(f, "]}");
    }
    fprintf(f, "]}\n");
    fflush(f);
    fclose(f);
    /* Atomic rename */
    if (rename(tmp_path, path) != 0) {
        unlink(tmp_path);
        return false;
    }
    /* Backup: copy successful save to .bak (atomic via temp+rename) */
    char bak_path[1024];
    snprintf(bak_path, sizeof bak_path, "%s.bak", path);
    char bak_tmp[1024];
    snprintf(bak_tmp, sizeof bak_tmp, "%s.bak.tmp", path);
    FILE *src = fopen(path, "r");
    if (src) {
        FILE *dst = fopen(bak_tmp, "w");
        if (dst) {
            char copybuf[8192];
            size_t n;
            while ((n = fread(copybuf, 1, sizeof copybuf, src)) > 0)
                fwrite(copybuf, 1, n, dst);
            fclose(dst);
            rename(bak_tmp, bak_path);  /* atomic */
        }
        fclose(src);
    }
    return true;
}

bool lmux_snapshot_load(lmux_app *app, const char *path) {
    if (!app || !path) return false;
    FILE *f = fopen(path, "r");
    if (!f) return false;
    char buf[65536];
    size_t n = fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    if (n == 0) return false;
    buf[n] = 0;

    /* Walk workspace objects in the workspaces array.
     * Find each "title":"..." / "cwd":"..." pair, create the workspace,
     * then find surfaces/panes inside it by brace matching. */
    const char *p = buf;

    /* Find workspaces array */
    const char *ws_arr = strstr(p, "\"workspaces\":[");
    if (!ws_arr) {
        /* No workspaces array — check if file was non-empty (corrupt) */
        if (n > 2) return false;
        return true; /* genuinely empty save */
    }
    ws_arr += 14;

    while (1) {
        /* Skip to next workspace object */
        while (*ws_arr && *ws_arr != '{') ws_arr++;
        if (*ws_arr != '{') break;

        /* Find the matching closing brace for this workspace */
        const char *ws_end = ws_arr + 1;
        int depth = 1;
        while (*ws_end && depth > 0) {
            if (*ws_end == '{') depth++;
            if (*ws_end == '}') depth--;
            ws_end++;
        }

        /* Extract fields from this workspace block */
        char wtitle[256] = {0};
        char wcwd[1024] = {0};

        /* Extract fields using json helpers */
        char buf_copy[4096];
        size_t blen = (size_t)(ws_end - ws_arr);
        if (blen >= sizeof buf_copy) blen = sizeof buf_copy - 1;
        memcpy(buf_copy, ws_arr, blen);
        buf_copy[blen] = 0;

        json_extract_string(buf_copy, "title", wtitle, sizeof wtitle);
        json_extract_string(buf_copy, "cwd", wcwd, sizeof wcwd);

        if (!wtitle[0]) { ws_arr = ws_end; continue; }

        lmux_workspace *ws = lmux_workspace_create(app, wtitle);
        if (wcwd[0]) lmux_workspace_set_cwd(ws, wcwd);

        /* Find surfaces array inside this workspace block */
        const char *sf_arr = strstr(buf_copy, "\"surfaces\":[");
        if (sf_arr) {
            sf_arr = strchr(sf_arr, '[');
            if (sf_arr) sf_arr++;

            while (1) {
                while (*sf_arr && *sf_arr != '{') sf_arr++;
                if (*sf_arr != '{') break;

                const char *sf_end = sf_arr + 1;
                int sd = 1;
                while (*sf_end && sd > 0) {
                    if (*sf_end == '{') sd++;
                    if (*sf_end == '}') sd--;
                    sf_end++;
                }

                char stitle[128] = {0};
                char sfcopy[2048];
                size_t sblen = (size_t)(sf_end - sf_arr);
                if (sblen >= sizeof sfcopy) sblen = sizeof sfcopy - 1;
                memcpy(sfcopy, sf_arr, sblen);
                sfcopy[sblen] = 0;

                json_extract_string(sfcopy, "title", stitle, sizeof stitle);

                lmux_surface *sf = NULL;
                if (stitle[0]) {
                    sf = lmux_surface_create(ws, stitle);
                    /* Remove the default pane surface_create added */
                    if (sf && sf->panes.len > 0) {
                        for (size_t pi = 0; pi < sf->panes.len; pi++) {
                            pane_kill_pty(sf->panes.items[pi]);
                            free(sf->panes.items[pi]);
                        }
                        sf->panes.len = 0;
                    }
                }

                /* Find panes array inside this surface */
                const char *pn_arr = strstr(sfcopy, "\"panes\":[");
                if (pn_arr && sf) {
                    pn_arr = strchr(pn_arr, '[');
                    if (pn_arr) pn_arr++;

                    while (1) {
                        while (*pn_arr && *pn_arr != '{') pn_arr++;
                        if (*pn_arr != '{') break;

                        const char *pn_end = pn_arr + 1;
                        int pd = 1;
                        while (*pn_end && pd > 0) {
                            if (*pn_end == '{') pd++;
                            if (*pn_end == '}') pd--;
                            pn_end++;
                        }

                        char pk[16] = {0}, pcmd[512] = {0};
                        char pncopy[1024];
                        size_t pblen = (size_t)(pn_end - pn_arr);
                        if (pblen >= sizeof pncopy) pblen = sizeof pncopy - 1;
                        memcpy(pncopy, pn_arr, pblen);
                        pncopy[pblen] = 0;

                        json_extract_string(pncopy, "kind", pk, sizeof pk);
                        json_extract_string(pncopy, "command", pcmd, sizeof pcmd);

                        /* Create pane on this surface (defer PTY spawn during restore) */
                        lmux_pane *np = lmux_pane_split(ws, sf,
                            LMUX_SPLIT_HORIZONTAL,
                            pcmd[0] ? pcmd : NULL, false);
                        if (np && pk[0] && strcmp(pk, "terminal") != 0) {
                            snprintf(np->kind, sizeof np->kind, "%s", pk);
                        }

                        pn_arr = pn_end;
                    }
                }

                sf_arr = sf_end;
            }
        }

        ws_arr = ws_end;
    }
    return true;
}

/* Wrapper: try main path, fall back to .bak on corruption */
bool lmux_snapshot_load_with_recovery(lmux_app *app, const char *path) {
    if (!app || !path) return false;
    /* Try main file first */
    if (lmux_snapshot_load(app, path)) return true;
    /* Main file corrupt or missing — try backup */
    char bak[1024];
    snprintf(bak, sizeof bak, "%s.bak", path);
    lmux_log(LMUX_LOG_WARN, "snapshot: main file failed, trying backup %s", bak);
    return lmux_snapshot_load(app, bak);
}

/* ------------------------------------------------------------------ */
/* Server helpers - accessors for opaque lmux_app                     */
/* ------------------------------------------------------------------ */

int lmux_server_get_listen_fd(const lmux_app *app) {
    if (!app) return -1;
    return app->listen_fd;
}
const char *lmux_app_boot_id(const lmux_app *app) {
    return app ? app->boot_id : "";
}

uint64_t lmux_app_event_seq(const lmux_app *app) {
    return app ? app->event_seq : 0;
}

void lmux_server_set_listen_fd(lmux_app *app, int fd) {
    if (app) app->listen_fd = fd;
}

void lmux_metrics_inc_requests(lmux_app *app) {
    if (app) app->metrics_requests++;
}
void lmux_metrics_inc_errors(lmux_app *app) {
    if (app) app->metrics_errors++;
}
void lmux_metrics_inc_connections(lmux_app *app) {
    if (app) app->metrics_connections++;
}

/* ------------------------------------------------------------------ */
/* Agent management (stubs — full implementation planned)             */
/* ------------------------------------------------------------------ */

lmux_id lmux_agent_spawn(lmux_app *app, const char *name, const char *command, lmux_id workspace_id) {
    (void)workspace_id;
    if (!app || !name || !command) return 0;
    lmux_agent *a = calloc(1, sizeof *a);
    a->id = app->next_agent_id++;
    snprintf(a->name, sizeof a->name, "%s", name);
    snprintf(a->command, sizeof a->command, "%s", command);
    a->pid = 0;
    a->status = 0;  /* stopped */
    a->workspace_id = workspace_id;
    a->started_at = time(NULL);
    vec_push(&app->agents, a);
    return a->id;
}

lmux_agent **lmux_agent_list(lmux_app *app, size_t *out_count) {
    if (!app) { *out_count = 0; return NULL; }
    *out_count = app->agents.len;
    if (app->agents.len == 0) return NULL;
    lmux_agent **list = malloc(app->agents.len * sizeof *list);
    for (size_t i = 0; i < app->agents.len; i++) {
        list[i] = app->agents.items[i];
    }
    return list;
}

bool lmux_agent_stop(lmux_app *app, lmux_id agent_id) {
    if (!app) return false;
    for (size_t i = 0; i < app->agents.len; i++) {
        lmux_agent *a = app->agents.items[i];
        if (a && a->id == agent_id) {
            if (a->pid > 0) kill(a->pid, SIGTERM);
            a->status = 0;
            return true;
        }
    }
    return false;
}

lmux_agent *lmux_agent_get(lmux_app *app, lmux_id agent_id) {
    if (!app) return NULL;
    for (size_t i = 0; i < app->agents.len; i++) {
        lmux_agent *a = app->agents.items[i];
        if (a && a->id == agent_id) return a;
    }
    return NULL;
}

void lmux_agent_update_status(lmux_app *app, pid_t pid, int status) {
    if (!app) return;
    for (size_t i = 0; i < app->agents.len; i++) {
        lmux_agent *a = app->agents.items[i];
        if (a && a->pid == pid) {
            a->status = status;
            if (status == 0) a->pid = 0;
        }
    }
}

/* ------------------------------------------------------------------ */
/* Agent hibernation                                                   */
/* ------------------------------------------------------------------ */

#define HIBERNATE_AFTER_SECONDS 300  /* 5 minutes idle */

bool lmux_agent_hibernate(lmux_app *app, lmux_id agent_id) {
    if (!app) return false;
    for (size_t i = 0; i < app->agents.len; i++) {
        lmux_agent *a = app->agents.items[i];
        if (a && a->id == agent_id) {
            if (a->hibernated || a->pid <= 0) return false;

            /* Save command for resume */
            if (a->command[0]) {
                snprintf(a->resume_cmd, sizeof(a->resume_cmd), "%s", a->command);
            }

            /* Kill the process */
            kill(a->pid, SIGTERM);
            usleep(100000);  /* 100ms grace period */
            if (kill(a->pid, 0) == 0) {
                kill(a->pid, SIGKILL);  /* force kill if still alive */
            }

            a->hibernated = true;
            a->hibernated_at = time(NULL);
            a->pid = 0;
            a->status = 0;

            lmux_event_push(app, "agent.hibernated", "agent", "\"id\":%u,\"name\":\"%s\"", a->id, a->name);
            return true;
        }
    }
    return false;
}

bool lmux_agent_resume(lmux_app *app, lmux_id agent_id) {
    if (!app) return false;
    for (size_t i = 0; i < app->agents.len; i++) {
        lmux_agent *a = app->agents.items[i];
        if (a && a->id == agent_id) {
            if (!a->hibernated) return false;

            /* Restart with saved command */
            const char *cmd = a->resume_cmd[0] ? a->resume_cmd : a->command;
            if (!cmd[0]) return false;

            pid_t pid = fork();
            if (pid == 0) {
                /* Child: exec the command */
                execl("/bin/sh", "sh", "-c", cmd, NULL);
                _exit(127);
            } else if (pid > 0) {
                a->pid = pid;
                a->hibernated = false;
                a->status = 1;
                a->last_activity = time(NULL);

                lmux_event_push(app, "agent.resumed", "agent", "\"id\":%u,\"name\":\"%s\"", a->id, a->name);
                return true;
            }
        }
    }
    return false;
}

void lmux_hibernation_check(lmux_app *app) {
    if (!app) return;
    time_t now = time(NULL);

    for (size_t i = 0; i < app->agents.len; i++) {
        lmux_agent *a = app->agents.items[i];
        if (!a || a->hibernated || a->pid <= 0) continue;

        /* Auto-hibernate if idle too long */
        if (a->last_activity > 0 && (now - a->last_activity) >= HIBERNATE_AFTER_SECONDS) {
            lmux_agent_hibernate(app, a->id);
        }
    }
}

/* ------------------------------------------------------------------ */
/* Focus history                                                      */
/* ------------------------------------------------------------------ */

void lmux_focus_history_init(struct lmux_focus_history *h) {
    if (!h) return;
    memset(h, 0, sizeof(*h));
}

void lmux_focus_history_push(struct lmux_focus_history *h,
                             const char *pane_id,
                             const char *workspace_id) {
    if (!h || !pane_id || !workspace_id) return;
    time_t now = time(NULL);

    /* Update the previous entry's duration if there is one */
    if (h->count > 0) {
        int prev_idx = (h->head - 1 + 100) % 100;
        struct lmux_focus_entry *prev = &h->entries[prev_idx];
        if (prev->timestamp > 0) {
            int64_t diff_ms = (int64_t)difftime(now, prev->timestamp) * 1000;
            if (diff_ms < 0) diff_ms = 0;
            prev->duration_ms = diff_ms;
        }
    }

    /* Write new entry */
    struct lmux_focus_entry *e = &h->entries[h->head];
    memset(e, 0, sizeof(*e));
    snprintf(e->pane_id, sizeof(e->pane_id), "%s", pane_id);
    snprintf(e->workspace_id, sizeof(e->workspace_id), "%s", workspace_id);
    e->timestamp = now;
    e->duration_ms = 0;

    h->head = (h->head + 1) % 100;
    if (h->count < 100) h->count++;
}

int lmux_focus_history_recent(struct lmux_focus_history *h,
                               int n,
                               struct lmux_focus_entry *out) {
    if (!h || !out || n <= 0) return 0;
    if (n > h->count) n = h->count;

    /* Walk backwards from head, most recent first */
    int written = 0;
    for (int i = 0; i < n; i++) {
        int idx = (h->head - 1 - i + 100) % 100;
        memcpy(&out[written], &h->entries[idx], sizeof(struct lmux_focus_entry));
        written++;
    }
    return written;
}

void lmux_focus_history_clear(struct lmux_focus_history *h) {
    if (!h) return;
    memset(h, 0, sizeof(*h));
}
