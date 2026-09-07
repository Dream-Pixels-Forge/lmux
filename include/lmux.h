/*
 * lmux.h - Public C ABI for the lmux core library.
 *
 * The core is a pure-C library that:
 *   - Wraps the terminal emulator (Ghostty's libghostty if available,
 *     a built-in minimal VT/xterm shim otherwise for testing and
 *     graceful degradation).
 *   - Parses OSC 9 / OSC 99 / OSC 777 notification sequences.
 *   - Maintains the workspace model: workspaces, surfaces (horizontal
 *     tabs), panes (splits), each with cwd, branch, PR, listening ports,
 *     latest notification.
 *   - Implements the JSON socket protocol over a Unix domain socket.
 *
 * The GTK4 app and the lmux CLI both link against this library. The
 * design intent is that the core has zero GTK/GLib UI dependencies so
 * the CLI can run on a headless box and the protocol can be tested
 * without a display.
 *
 * This header is C-callable. The library is built as liblmux_core.so
 * (or .a) by the pnpm/npm build script.
 */
#ifndef LMUX_H
#define LMUX_H

#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include <time.h>
#include <sys/types.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------------------ */
/* Versioning                                                          */
/* ------------------------------------------------------------------ */

#define LMUX_VERSION_MAJOR 1
#define LMUX_VERSION_MINOR 0
#define LMUX_VERSION_PATCH 0
#define LMUX_VERSION "1.0.0"

const char *lmux_version(void);

/* ------------------------------------------------------------------ */
/* Logging                                                             */
/* ------------------------------------------------------------------ */

typedef enum {
    LMUX_LOG_DEBUG = 0,
    LMUX_LOG_INFO  = 1,
    LMUX_LOG_WARN  = 2,
    LMUX_LOG_ERROR = 3,
} lmux_log_level;

void lmux_log_set_level(lmux_log_level level);
void lmux_log(lmux_log_level level, const char *fmt, ...)
    __attribute__((format(printf, 2, 3)));
void lmux_log_request(const char *request_id, const char *cmd, bool ok, const char *detail);

/* ------------------------------------------------------------------ */
/* Opaque handles                                                      */
/* ------------------------------------------------------------------ */

typedef struct lmux_app      lmux_app;
typedef struct lmux_workspace lmux_workspace;
typedef struct lmux_surface   lmux_surface;
typedef struct lmux_pane      lmux_pane;

/* Workspace id type. Stable for the lifetime of the app. */
typedef uint32_t lmux_id;

/* ------------------------------------------------------------------ */
/* Workspace metadata - what the sidebar renders per row.              */
/* ------------------------------------------------------------------ */

typedef struct {
    char    id[64];           /* opaque workspace id (UUID-like)       */
    char    title[256];       /* user-facing title                    */
    char    cwd[1024];        /* working directory (full path)        */
    char    git_branch[128];  /* current git branch, "" if not a repo */
    char    pr_label[64];     /* e.g. "PR #42" or ""                  */
    char    ports[256];       /* comma-separated listening ports      */
    char    last_notify[512]; /* most recent notification text        */
    bool    unread;           /* true if any notification unread      */
    bool    waiting;          /* true if agent waiting for input      */
} lmux_workspace_info;

typedef struct {
    lmux_id id;                /* surface id                            */
    char    title[128];        /* horizontal-tab title                  */
    bool    focused;           /* true if currently focused surface     */
} lmux_surface_info;

typedef struct {
    lmux_id id;                /* pane id                               */
    char    kind[16];          /* "terminal" | "browser"                */
    bool    focused;           /* true if focused within its surface    */
} lmux_pane_info;

/* ------------------------------------------------------------------ */
/* App lifecycle                                                       */
/* ------------------------------------------------------------------ */

lmux_app *lmux_app_new(const char *socket_path);
void      lmux_app_free(lmux_app *app);

/* Install a poll-style loop hook: app polls events every ~16ms.
 * The host (GTK) is expected to call lmux_app_tick from its main
 * loop, or use the GLib integration helper below. */
void lmux_app_tick(lmux_app *app);

/* Blocking run loop for headless / CLI usage. Returns when quit. */
int  lmux_app_run(lmux_app *app);
void lmux_app_quit(lmux_app *app, int exit_code);

/* Set the running flag so the main loop will execute.
 * Call before a custom event loop that uses lmux_app_is_running(). */
void lmux_app_start(lmux_app *app);

/* Check if the app is still running. */
bool lmux_app_is_running(const lmux_app *app);

/* ------------------------------------------------------------------ */
/* Workspace operations                                                */
/* ------------------------------------------------------------------ */

lmux_workspace *lmux_workspace_create(lmux_app *app, const char *title);
void            lmux_workspace_close (lmux_app *app, lmux_workspace *ws);
lmux_workspace *lmux_workspace_ssh_create(lmux_app *app, const char *ssh_target, int port);
size_t          lmux_workspace_count (lmux_app *app);
lmux_workspace *lmux_workspace_focused(lmux_app *app);
lmux_workspace *lmux_workspace_by_index(lmux_app *app, size_t index);
lmux_workspace *lmux_workspace_by_id(lmux_app *app, lmux_id id);

bool lmux_workspace_get_info(lmux_workspace *ws, lmux_workspace_info *out);

void lmux_workspace_set_title(lmux_workspace *ws, const char *title);
void lmux_workspace_set_cwd  (lmux_workspace *ws, const char *cwd);

/* Refresh git/ports/branch metadata. Idempotent and cheap. */
void lmux_workspace_refresh(lmux_workspace *ws);

/* ------------------------------------------------------------------ */
/* Surface (horizontal tab) operations                                 */
/* ------------------------------------------------------------------ */

lmux_surface *lmux_surface_create(lmux_workspace *ws, const char *title);
void          lmux_surface_close (lmux_workspace *ws, lmux_surface *s);
size_t        lmux_surface_count (lmux_workspace *ws);
lmux_surface *lmux_surface_focused(lmux_workspace *ws);
bool          lmux_surface_get_info   (lmux_surface *s, lmux_surface_info *out);
void          lmux_surface_set_title(lmux_surface *s, const char *title);

/* ------------------------------------------------------------------ */
/* Pane (split) operations                                             */
/* ------------------------------------------------------------------ */

typedef enum {
    LMUX_SPLIT_HORIZONTAL = 0, /* new pane to the right of current  */
    LMUX_SPLIT_VERTICAL   = 1, /* new pane below the current pane   */
} lmux_split_dir;

lmux_pane *lmux_pane_split(lmux_workspace *ws, lmux_surface *s,
                           lmux_split_dir dir, const char *command,
                           bool spawn_pty);
void       lmux_pane_close(lmux_workspace *ws, lmux_pane *p);
size_t     lmux_pane_count(lmux_workspace *ws);
lmux_pane *lmux_pane_focused(lmux_workspace *ws);
void       lmux_pane_focus  (lmux_workspace *ws, lmux_pane *p);
void       lmux_pane_focus_dir(lmux_workspace *ws,
                               int dx, int dy);   /* -1,0,+1 each     */

bool lmux_pane_get_info(lmux_pane *p, lmux_pane_info *out);

/* Send keystrokes to a terminal pane. Text is interpreted as
 * key-name syntax: literal chars, C-X for Ctrl-X, M-x for Alt-x. */
void lmux_pane_send_keys(lmux_pane *p, const char *keys);
bool lmux_pane_pty_set_size(lmux_pane *p, int cols, int rows);

/* Browser pane: open URL, evaluate JS, snapshot a11y tree. */
void lmux_browser_open_url(lmux_pane *p, const char *url);
char *lmux_browser_eval_js(lmux_pane *p, const char *script); /* free with free() */
char *lmux_browser_snapshot(lmux_pane *p);                    /* free with free() */

/* ------------------------------------------------------------------ */
/* Notifications                                                       */
/* ------------------------------------------------------------------ */

typedef struct {
    uint64_t seq;             /* monotonic, 1-based                  */
    char     text[1024];      /* body                                */
    char     workspace_id[64];/* owning workspace                    */
    bool     waiting;         /* true if agent is waiting for input  */
    time_t   created_at;      /* time of creation (time(NULL))         */
} lmux_notification;

/* Notification ring — visual indicator that a pane needs attention */
typedef struct {
    lmux_id  pane_id;
    bool     active;          /* true if ring is showing */
    time_t   activated_at;    /* when ring was activated */
    char     reason[256];     /* why ring was activated */
} lmux_notification_ring;

/* Notification hook — composable filter/transform/redirect */
#define LMUX_MAX_HOOKS 32
typedef struct {
    char event[64];           /* "agent.output", "agent.error", etc. */
    char filter[256];         /* JSONPath filter expression */
    char transform[512];      /* transform script */
    char redirect[256];       /* redirect target */
    bool enabled;
} lmux_notification_hook;

void lmux_notify(lmux_app *app, const char *text, bool waiting);
void lmux_workspace_notify(lmux_workspace *ws, const char *text, bool waiting);
void lmux_desktop_notify(const char *title, const char *body);

size_t               lmux_notification_count(lmux_app *app);
const lmux_notification *lmux_notification_at(lmux_app *app, size_t index);
void                 lmux_notification_mark_read(lmux_app *app, uint64_t seq);

/* Notification rings */
bool lmux_notification_ring_add(lmux_app *app, lmux_id pane_id, const char *reason);
bool lmux_notification_ring_clear(lmux_app *app, lmux_id pane_id);
size_t lmux_notification_ring_count(lmux_app *app);
const lmux_notification_ring *lmux_notification_ring_at(lmux_app *app, size_t index);

/* Notification hooks */
int  lmux_notification_hook_add(lmux_app *app, const char *event, const char *filter,
                                 const char *transform, const char *redirect);
bool lmux_notification_hook_remove(lmux_app *app, const char *event);
size_t lmux_notification_hook_count(lmux_app *app);
const lmux_notification_hook *lmux_notification_hook_at(lmux_app *app, size_t index);

/* ------------------------------------------------------------------ */
/* Window management                                                   */
/* ------------------------------------------------------------------ */

typedef struct {
    lmux_id  id;
    char     title[256];
    lmux_id  workspace_ids[64];   /* workspaces in this window */
    size_t   workspace_count;     /* number of workspaces */
    lmux_id  focused_workspace;   /* currently focused workspace in window */
    int      x, y;               /* position */
    int      width, height;      /* size */
    bool     fullscreen;
    time_t   created_at;
} lmux_window;

lmux_window *lmux_window_create(lmux_app *app, const char *title);
void lmux_window_close(lmux_app *app, lmux_id window_id);
lmux_window *lmux_window_by_id(lmux_app *app, lmux_id id);
bool lmux_window_focus(lmux_app *app, lmux_id window_id);
bool lmux_window_move_workspace(lmux_app *app, lmux_id window_id, lmux_id workspace_id);
size_t lmux_window_count(lmux_app *app);
const lmux_window *lmux_window_at(lmux_app *app, size_t index);

/* ------------------------------------------------------------------ */
/* Persistent SSH PTY                                                  */
/* ------------------------------------------------------------------ */

typedef struct {
    lmux_id  id;
    char     host[256];           /* SSH host */
    char     user[128];           /* SSH user */
    char     key_path[512];       /* path to SSH key */
    char     pane_id_str[64];     /* associated pane */
    pid_t    ssh_pid;             /* SSH process ID */
    time_t   connected_at;        /* when connected */
    time_t   last_activity;       /* last activity */
    bool     active;              /* true if session is active */
    char     session_file[512];   /* path to session file for persistence */
} lmux_ssh_session;

lmux_ssh_session *lmux_ssh_session_create(lmux_app *app, const char *host,
                                            const char *user, const char *key_path);
bool lmux_ssh_session_attach(lmux_ssh_session *s, lmux_id pane_id);
bool lmux_ssh_session_detach(lmux_app *app, lmux_id session_id);
bool lmux_ssh_session_kill(lmux_app *app, lmux_id session_id);
size_t lmux_ssh_session_count(lmux_app *app);
const lmux_ssh_session *lmux_ssh_session_at(lmux_app *app, size_t index);
bool lmux_ssh_session_save(lmux_app *app, const char *path);
bool lmux_ssh_session_restore(lmux_app *app, const char *path);

/* ------------------------------------------------------------------ */
/* Feed Panel                                                          */
/* ------------------------------------------------------------------ */

typedef struct {
    lmux_id  id;
    char     title[256];
    char     filter[256];          /* filter expression (event type, agent, etc. */
    size_t   max_entries;          /* max entries to keep */
    bool     auto_scroll;          /* auto-scroll to bottom */
    time_t   created_at;
} lmux_feed_panel;

typedef struct {
    lmux_id  panel_id;
    uint64_t seq;                  /* monotonic sequence */
    char     event_type[64];       /* "agent.output", "notification", etc. */
    char     source[128];          /* source agent/workspace */
    char     text[2048];           /* content */
    time_t   timestamp;
} lmux_feed_entry;

lmux_feed_panel *lmux_feed_panel_create(lmux_app *app, const char *title, const char *filter);
bool lmux_feed_panel_close(lmux_app *app, lmux_id panel_id);
lmux_feed_panel *lmux_feed_panel_by_id(lmux_app *app, lmux_id id);
size_t lmux_feed_panel_count(lmux_app *app);
const lmux_feed_panel *lmux_feed_panel_at(lmux_app *app, size_t index);

bool lmux_feed_entry_add(lmux_app *app, lmux_id panel_id, const char *event_type,
                          const char *source, const char *text);
size_t lmux_feed_entry_count(lmux_app *app, lmux_id panel_id);
const lmux_feed_entry *lmux_feed_entry_at(lmux_app *app, lmux_id panel_id, size_t index);
bool lmux_feed_panel_clear(lmux_app *app, lmux_id panel_id);

/* ------------------------------------------------------------------ */
/* Event queue                                                         */
/* ------------------------------------------------------------------ */

/* Push a formatted event JSON string onto the event queue. */
void lmux_event_push(lmux_app *app, const char *name, const char *category, const char *body_fmt, ...);

/* Return the number of pending events. */
size_t lmux_events_pending(lmux_app *app);
const char *lmux_app_boot_id(const lmux_app *app);
uint64_t    lmux_app_event_seq(const lmux_app *app);

/* Pop and return the oldest event string. Caller must free(). */
char *lmux_event_pop(lmux_app *app);

/* ------------------------------------------------------------------ */
/* Session snapshot / restore (best-effort, atomic write)              */
/* ------------------------------------------------------------------ */

typedef struct {
    uint32_t version;          /* bump when layout schema changes      */
    char     cwd[1024];
    char     command[512];
    int      split_dir;        /* 0=H 1=V, -1 for root                  */
} lmux_pane_snapshot;

typedef struct {
    char    title[256];
    char    cwd[1024];
    size_t  pane_count;
    lmux_pane_snapshot panes[32];
} lmux_surface_snapshot;

typedef struct {
    char    title[256];
    char    cwd[1024];
    size_t  surface_count;
    lmux_surface_snapshot surfaces[16];
} lmux_workspace_snapshot;

bool lmux_snapshot_save (const lmux_app *app, const char *path);
bool lmux_snapshot_load (lmux_app *app, const char *path);
bool lmux_snapshot_load_with_recovery(lmux_app *app, const char *path);

/* ------------------------------------------------------------------ */
/* OSC escape parser - public for testing and reuse.                  */
/* ------------------------------------------------------------------ */

typedef struct lmux_osc_parser lmux_osc_parser;

lmux_osc_parser *lmux_osc_parser_new(void);
void             lmux_osc_parser_free(lmux_osc_parser *p);

/* Feed bytes. When a complete OSC 9 / 99 / 777 sequence is parsed,
 * *out_text is filled with the body (caller owns the buffer's lifetime
 * until the next feed call) and the function returns true. */
bool lmux_osc_parser_feed(lmux_osc_parser *p, const char *bytes, size_t n,
                          const char **out_text, bool *out_waiting);

/* ------------------------------------------------------------------ */
/* Socket protocol                                                    */
/* ------------------------------------------------------------------ */

/* The socket path is set when the app is constructed. External
 * clients (including the lmux CLI) connect, send a single
 * JSON request {"cmd": "...", "args": {...}}, and receive a single
 * JSON response {"ok": bool, "result": ..., "error": "..."}.  */
const char *lmux_socket_path(const lmux_app *app);

/* For embedders that want to dispatch JSON requests directly. */

/* ------------------------------------------------------------------ */
/* Unix socket server                                                   */
/* ------------------------------------------------------------------ */

/* Start the socket server (blocking accept). Returns 0 on success. */
int lmux_server_start(lmux_app *app);

/* Start the socket server in a background thread. Returns 0 on success. */
int lmux_server_start_threaded(lmux_app *app);

/* Non-blocking tick: accept and serve one pending connection.
 * Returns 1 if a request was handled, 0 otherwise. Call from the
 * host's main loop (e.g. GTK idle callback). */
int lmux_server_tick(lmux_app *app);

/* Stop the server, close the socket, remove the socket file. */
void lmux_server_stop(lmux_app *app);

/* Accessors for server internals (used by server.c, opaque to callers). */
int  lmux_server_get_listen_fd(const lmux_app *app);
void lmux_server_set_listen_fd(lmux_app *app, int fd);

/* Metrics accessors (server.c increments, model.c reads). */
void lmux_metrics_inc_requests(lmux_app *app);
void lmux_metrics_inc_errors(lmux_app *app);
void lmux_metrics_inc_connections(lmux_app *app);

/* ------------------------------------------------------------------ */
/* Configuration                                                        */
/* ------------------------------------------------------------------ */

typedef struct {
    void **items;
    size_t len;
    size_t cap;
} lmux_config_vec;

typedef struct {
    char key[64];
    char action[128];
} lmux_keybinding;

typedef struct {
    char name[64];
    char fg[32];
    char bg[32];
    char cursor[32];
} lmux_theme_entry;

typedef struct {
    char name[128];
    lmux_config_vec workspace_ids;   /* lmux_id* items */
} lmux_workspace_group;

typedef struct {
    char font_family[128];
    int  font_size;
    char theme[64];
    int  scrollback_lines;
    bool show_sidebar;
    bool show_notifications_panel;
    bool auto_save_session;
    int  auto_save_interval_sec;
    lmux_config_vec keybindings;       /* lmux_keybinding* items */
    lmux_config_vec themes;            /* lmux_theme_entry* items */
    lmux_config_vec workspace_groups;  /* lmux_workspace_group* items */
    char default_shell[256];
    char agent_paths[5][512];  /* claude-code, opencode, codex, aider, goose */
} lmux_config;

lmux_config *lmux_config_new(void);
void         lmux_config_free(lmux_config *cfg);
const char  *lmux_config_path(char *buf, size_t cap);
bool         lmux_config_load(lmux_config *cfg, const char *path);
bool         lmux_config_load_buf(lmux_config *cfg, const char *buf, size_t len);
bool         lmux_config_save(const lmux_config *cfg, const char *path);
lmux_keybinding  *lmux_config_find_key(lmux_config *cfg, const char *key);
lmux_theme_entry *lmux_config_find_theme(lmux_config *cfg, const char *name);
lmux_workspace_group *lmux_workspace_group_find(lmux_config *cfg, const char *name);
lmux_workspace_group *lmux_workspace_group_create(lmux_config *cfg, const char *name);
bool  lmux_workspace_group_add(lmux_config *cfg, const char *name, lmux_id ws_id);
bool  lmux_workspace_group_remove(lmux_config *cfg, const char *name, lmux_id ws_id);

/* ------------------------------------------------------------------ */
/* Agent management                                                     */
/* ------------------------------------------------------------------ */

typedef struct {
    char     name[64];           /* "claude-code", "opencode", "codex", or custom */
    char     command[1024];      /* full command to launch */
    pid_t    pid;                /* 0 if not running */
    int      status;             /* 0=stopped, 1=running, 2=waiting_input */
    lmux_id  workspace_id;      /* workspace this agent is associated with */
    lmux_id  pane_id;            /* pane the agent runs in */
    time_t   started_at;         /* when agent was started */
    lmux_id  id;                 /* unique agent id */
    /* Hibernation support */
    bool     hibernated;         /* true if agent is hibernated (killed to save resources) */
    time_t   last_activity;      /* timestamp of last activity */
    time_t   hibernated_at;      /* when agent was hibernated */
    char     resume_cmd[1024];   /* command to resume the agent */
} lmux_agent;

/* Spawn an agent process in a new pane. Returns the agent id, or 0 on failure. */
lmux_id lmux_agent_spawn(lmux_app *app, const char *name, const char *command, lmux_id workspace_id);

/* List agents - caller must free returned array */
lmux_agent **lmux_agent_list(lmux_app *app, size_t *out_count);

/* Stop an agent by id. Returns true on success. */
bool lmux_agent_stop(lmux_app *app, lmux_id agent_id);

/* Get agent by id */
lmux_agent *lmux_agent_get(lmux_app *app, lmux_id agent_id);

/* Update agent status (called when pane process exits) */
void lmux_agent_update_status(lmux_app *app, pid_t pid, int status);

/* Hibernate an agent — kills process to save resources */
bool lmux_agent_hibernate(lmux_app *app, lmux_id agent_id);

/* Resume a hibernated agent — restarts with saved command */
bool lmux_agent_resume(lmux_app *app, lmux_id agent_id);

/* Check and hibernate idle agents (called from event loop) */
void lmux_hibernation_check(lmux_app *app);

/* ------------------------------------------------------------------ */
/* Wait points (synchronization primitive)                              */
/* ------------------------------------------------------------------ */

typedef struct {
    char name[256];
    bool signaled;
} lmux_wait_point;
/* ------------------------------------------------------------------ */
/* Server-internal helpers (public for embedders)                      */
/* ------------------------------------------------------------------ */

#ifdef LMUX_INTERNAL
/* Accept and serve one client on a connected fd. */
void lmux_server_serve_client(lmux_app *app, int client_fd);
#endif
char *lmux_dispatch_json(lmux_app *app, const char *json_request);

/* ------------------------------------------------------------------ */
/* Session auto-save/restore                                            */
/* ------------------------------------------------------------------ */

void lmux_app_auto_save(lmux_app *app);
void lmux_app_auto_restore(lmux_app *app);
/* ------------------------------------------------------------------ */
/* Focus history                                                      */
/* ------------------------------------------------------------------ */

typedef struct lmux_focus_entry {
    char   pane_id[64];
    char   workspace_id[64];
    time_t timestamp;
    int64_t duration_ms;
} lmux_focus_entry;

typedef struct lmux_focus_history {
    lmux_focus_entry entries[100];
    int count;
    int head;  /* ring buffer write cursor */
} lmux_focus_history;

void lmux_focus_history_init(struct lmux_focus_history *h);
void lmux_focus_history_push(struct lmux_focus_history *h,
                             const char *pane_id,
                             const char *workspace_id);
int  lmux_focus_history_recent(struct lmux_focus_history *h,
                                int n,
                                struct lmux_focus_entry *out);
void lmux_focus_history_clear(struct lmux_focus_history *h);


#ifdef __cplusplus
}
#endif

#endif /* LMUX_H */
