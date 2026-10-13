/*
 * control.c - tmux-compatible control-mode framing + event fan-out for lmux.
 *
 * This layer is framing + fan-out ONLY. It never dispatches commands itself:
 * every command line is dispatched through lmux_dispatch_json() by the
 * server's control loop (server.c), so there is exactly one dispatcher.
 *
 * Wire protocol (v1, subset of tmux -C):
 *
 *   Client -> server : one JSON request per line, e.g.
 *                      {"cmd":"workspace.create","args":{"title":"x"}}
 *   Server -> client : %begin <epoch> <seq> 0
 *                      <single-line JSON response>
 *                      %end <epoch> <seq> 0        (ok:true)
 *                      %error <epoch> <seq> 0      (ok:false, replaces %end)
 *
 *   Async notifications (never inside a %begin/%end block; deferred until
 *   the block closes, mirroring tmux's guard_depth):
 *                      %layout-change <surface-id> <pane-count>
 *                      %session-changed <workspace-id> <title>
 *                      %window-pane-changed <surface-id> <pane-id>
 *                      %output <pane-id> <text...>
 *                      %subscription-changed <name> <1|0>
 *                      %dropped <n>                (backpressure overflow)
 *
 * Subscriptions (refresh-client -B name:what:format, tmux form):
 *   - subscribe:   {"cmd":"refresh-client","args":{"subscription":"name:what:format"}}
 *   - unsubscribe: {"cmd":"refresh-client","args":{"unsubscribe":"name"}}
 *   `what` filters notification classes by substring: "layout", "session",
 *   "pane", "output". No subscriptions => all classes delivered.
 *
 * Backpressure (documented approach, tmux-inspired):
 *   - per-client pending queue capped at 1024 lines;
 *   - overflow drops oldest %output lines first (never control acks);
 *   - drops are counted and reported as %dropped <n> on next flush;
 *   - %output is best-effort streaming; command responses are never dropped
 *     (a slow client only delays its own thread, never the model: dispatch
 *     holds the model lock only while formatting, fan-out itself never
 *     blocks on a client socket).
 *
 * Threading: every accepted connection runs on its own thread (server.c).
 * The registry + each client's queues are mutex-guarded. The originating
 * thread sets its slot id thread-locally before dispatch so the
 * refresh-client handler can find its own subscription set without any
 * dispatch signature change.
 */

#define _POSIX_C_SOURCE 200809L

#include "lmux.h"

#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define LMUX_CONTROL_MAX_CLIENTS 32
#define LMUX_CONTROL_MAX_PENDING 1024
#define LMUX_CONTROL_MAX_SUBS 16

typedef struct {
    char name[64];
    char what[128];
    char format[128];
} control_sub;

typedef struct {
    bool     in_use;
    bool     in_block;          /* %begin open on this client: defer async */
    control_sub subs[LMUX_CONTROL_MAX_SUBS];
    size_t   nsubs;
    char   **pending;           /* async lines, outside blocks */
    size_t   npending, pending_cap;
    char   **deferred;          /* async lines arrived mid-block */
    size_t   ndeferred, deferred_cap;
    unsigned long dropped;      /* %output lines dropped to backpressure */
    pthread_mutex_t lock;
} control_client;

static control_client g_clients[LMUX_CONTROL_MAX_CLIENTS];
static pthread_mutex_t g_registry_lock = PTHREAD_MUTEX_INITIALIZER;
static int g_registry_init = 0;

/* Thread-local slot of the connection currently dispatching on this thread.
 * -1 when the thread serves a one-shot (non-control) client. */
static __thread int tl_control_slot = -1;

static void registry_ensure(void) {
    pthread_mutex_lock(&g_registry_lock);
    if (!g_registry_init) {
        for (int i = 0; i < LMUX_CONTROL_MAX_CLIENTS; i++)
            pthread_mutex_init(&g_clients[i].lock, NULL);
        g_registry_init = 1;
    }
    pthread_mutex_unlock(&g_registry_lock);
}

static void vec_push_line(char ***vec, size_t *n, size_t *cap, char *line) {
    if (*n >= *cap) {
        size_t ncap = *cap ? *cap * 2 : 16;
        char **nv = realloc(*vec, ncap * sizeof *nv);
        if (!nv) { free(line); return; }
        *vec = nv;
        *cap = ncap;
    }
    (*vec)[(*n)++] = line;
}

/* Class filter: no subscriptions => everything; else `what` must contain
 * the notification class substring. Class "subscription" (acks) is
 * control-plane and always delivered. */
static bool sub_matches(control_client *c, const char *cls) {
    if (strcmp(cls, "subscription") == 0) return true;
    if (c->nsubs == 0) return true;
    for (size_t i = 0; i < c->nsubs; i++) {
        if (c->subs[i].what[0] == '\0') return true;
        if (strstr(c->subs[i].what, cls) != NULL) return true;
    }
    return false;
}

int lmux_control_register(void) {
    registry_ensure();
    pthread_mutex_lock(&g_registry_lock);
    for (int i = 0; i < LMUX_CONTROL_MAX_CLIENTS; i++) {
        if (!g_clients[i].in_use) {
            control_client *c = &g_clients[i];
            pthread_mutex_lock(&c->lock);
            c->in_use = true;
            c->in_block = false;
            c->nsubs = 0;
            c->dropped = 0;
            /* Queues are empty on (re)use; free defensively. */
            for (size_t k = 0; k < c->npending; k++) free(c->pending[k]);
            for (size_t k = 0; k < c->ndeferred; k++) free(c->deferred[k]);
            c->npending = 0;
            c->ndeferred = 0;
            pthread_mutex_unlock(&c->lock);
            pthread_mutex_unlock(&g_registry_lock);
            return i;
        }
    }
    pthread_mutex_unlock(&g_registry_lock);
    return -1;
}

void lmux_control_unregister(int slot) {
    if (slot < 0 || slot >= LMUX_CONTROL_MAX_CLIENTS) return;
    registry_ensure();
    pthread_mutex_lock(&g_registry_lock);
    control_client *c = &g_clients[slot];
    pthread_mutex_lock(&c->lock);
    if (c->in_use) {
        for (size_t k = 0; k < c->npending; k++) free(c->pending[k]);
        for (size_t k = 0; k < c->ndeferred; k++) free(c->deferred[k]);
        free(c->pending);   c->pending = NULL;
        c->npending = 0;    c->pending_cap = 0;
        free(c->deferred);  c->deferred = NULL;
        c->ndeferred = 0;   c->deferred_cap = 0;
        c->nsubs = 0;
        c->in_use = false;
    }
    pthread_mutex_unlock(&c->lock);
    pthread_mutex_unlock(&g_registry_lock);
}

int lmux_control_current_slot(void) {
    return tl_control_slot;
}

void lmux_control_set_thread_slot(int slot) {
    tl_control_slot = slot;
}

void lmux_control_begin_block(int slot) {
    if (slot < 0 || slot >= LMUX_CONTROL_MAX_CLIENTS) return;
    pthread_mutex_lock(&g_clients[slot].lock);
    g_clients[slot].in_block = true;
    pthread_mutex_unlock(&g_clients[slot].lock);
}

void lmux_control_end_block(int slot) {
    if (slot < 0 || slot >= LMUX_CONTROL_MAX_CLIENTS) return;
    pthread_mutex_lock(&g_clients[slot].lock);
    g_clients[slot].in_block = false;
    /* Move deferred lines to pending (order-preserving) so the server
     * flushes them right after %end — tmux flushes deferred notifications
     * when the outermost guard block closes. */
    for (size_t k = 0; k < g_clients[slot].ndeferred; k++)
        vec_push_line(&g_clients[slot].pending,
                      &g_clients[slot].npending,
                      &g_clients[slot].pending_cap,
                      g_clients[slot].deferred[k]);
    g_clients[slot].ndeferred = 0;
    pthread_mutex_unlock(&g_clients[slot].lock);
}

int lmux_control_subscribe(int slot, const char *name,
                           const char *what, const char *format) {
    if (slot < 0 || slot >= LMUX_CONTROL_MAX_CLIENTS) return -1;
    if (!name || !name[0]) return -1;
    control_client *c = &g_clients[slot];
    pthread_mutex_lock(&c->lock);
    if (!c->in_use) { pthread_mutex_unlock(&c->lock); return -1; }
    for (size_t i = 0; i < c->nsubs; i++) {
        if (strcmp(c->subs[i].name, name) == 0) {
            /* Replace existing subscription. */
            if (what) snprintf(c->subs[i].what, sizeof c->subs[i].what, "%s", what);
            if (format) snprintf(c->subs[i].format, sizeof c->subs[i].format, "%s", format);
            pthread_mutex_unlock(&c->lock);
            return 0;
        }
    }
    if (c->nsubs >= LMUX_CONTROL_MAX_SUBS) {
        pthread_mutex_unlock(&c->lock);
        return -1;
    }
    control_sub *s = &c->subs[c->nsubs++];
    snprintf(s->name, sizeof s->name, "%s", name);
    snprintf(s->what, sizeof s->what, "%s", what ? what : "");
    snprintf(s->format, sizeof s->format, "%s", format ? format : "");
    pthread_mutex_unlock(&c->lock);
    return 0;
}

int lmux_control_unsubscribe(int slot, const char *name) {
    if (slot < 0 || slot >= LMUX_CONTROL_MAX_CLIENTS) return -1;
    if (!name || !name[0]) return -1;
    control_client *c = &g_clients[slot];
    pthread_mutex_lock(&c->lock);
    for (size_t i = 0; i < c->nsubs; i++) {
        if (strcmp(c->subs[i].name, name) == 0) {
            c->subs[i] = c->subs[c->nsubs - 1];
            c->nsubs--;
            pthread_mutex_unlock(&c->lock);
            return 0;
        }
    }
    pthread_mutex_unlock(&c->lock);
    return -1;
}

void lmux_control_notify(const char *cls, const char *line) {
    if (!cls || !line || !line[0]) return;
    registry_ensure();
    /* Copy the line once per interested client. Fan-out never blocks on a
     * socket: enqueue only; the owning thread writes. */
    for (int i = 0; i < LMUX_CONTROL_MAX_CLIENTS; i++) {
        control_client *c = &g_clients[i];
        /* Peek without the registry lock: in_use flips only under both
         * locks; taking the client lock alone is sufficient here. */
        pthread_mutex_lock(&c->lock);
        if (!c->in_use || !sub_matches(c, cls)) {
            pthread_mutex_unlock(&c->lock);
            continue;
        }
        char *dup = strdup(line);
        if (!dup) { pthread_mutex_unlock(&c->lock); continue; }
        if (c->in_block) {
            vec_push_line(&c->deferred, &c->ndeferred, &c->deferred_cap, dup);
        } else if (c->npending < LMUX_CONTROL_MAX_PENDING) {
            vec_push_line(&c->pending, &c->npending, &c->pending_cap, dup);
        } else {
            /* Backpressure: drop oldest %output first, never acks. */
            size_t drop_at = c->npending; /* sentinel: none found */
            for (size_t k = 0; k < c->npending; k++) {
                if (strncmp(c->pending[k], "%output ", 8) == 0) {
                    drop_at = k;
                    break;
                }
            }
            if (drop_at < c->npending) {
                free(c->pending[drop_at]);
                memmove(&c->pending[drop_at], &c->pending[drop_at + 1],
                        (c->npending - drop_at - 1) * sizeof(char *));
                c->npending--;
                c->dropped++;
                vec_push_line(&c->pending, &c->npending, &c->pending_cap, dup);
            } else {
                /* No %output to shed: drop the incoming line itself. */
                free(dup);
                c->dropped++;
            }
        }
        pthread_mutex_unlock(&c->lock);
    }
}

/* Pop up to `cap` pending lines for `slot` into `out` (caller frees each).
 * Returns the number of lines. Prepends a %dropped line when overflow
 * happened since the last pop. */
size_t lmux_control_pop_pending(int slot, char **out, size_t cap) {
    if (slot < 0 || slot >= LMUX_CONTROL_MAX_CLIENTS || !out || cap == 0)
        return 0;
    control_client *c = &g_clients[slot];
    pthread_mutex_lock(&c->lock);
    size_t n = 0;
    if (c->dropped > 0 && n < cap) {
        char tmp[64];
        snprintf(tmp, sizeof tmp, "%%dropped %lu", c->dropped);
        c->dropped = 0;
        out[n++] = strdup(tmp);
    }
    while (c->npending > 0 && n < cap) {
        out[n++] = c->pending[0];
        memmove(&c->pending[0], &c->pending[1],
                (c->npending - 1) * sizeof(char *));
        c->npending--;
    }
    pthread_mutex_unlock(&c->lock);
    return n;
}

void lmux_control_format_guard(char *out, size_t cap,
                               const char *kind, long epoch,
                               unsigned seq) {
    if (!out || cap == 0) return;
    snprintf(out, cap, "%%%s %ld %u 0", kind, epoch, seq);
}
