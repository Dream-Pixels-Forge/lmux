/*
 * server.c - Unix domain socket JSON protocol server for lmux.
 *
 * Listens on a Unix domain socket (stream). Each connection reads one
 * newline-delimited JSON request, dispatches it through
 * lmux_dispatch_json, and writes the JSON response. After responding,
 * the connection is closed (one-shot per the lmux protocol contract).
 *
 * This file is a portable C implementation that works on any POSIX
 * system with standard sockets. No GLib, no libevent — a blocking
 * accept loop that the host (GTK app or headless CLI) can either
 * run in a dedicated thread or integrate via lmux_app_tick.
 *
 * Protocol:
 *   Request:  {"cmd":"<name>","args":{...}}\n
 *   Response: {"ok":true,"result":...}\n
 *
 * Build:
 *   cc -c server.c -I../include -o server.o
 */

#define _GNU_SOURCE      /* SO_PEERCRED, struct ucred */
#define _POSIX_C_SOURCE 200809L

#include "lmux.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <pthread.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <poll.h>
#include <pwd.h>

/* Maximum request body we accept (including newline). */
#define LMUX_MAX_REQUEST 65536

/* Rate limiting constants */
#define RATE_LIMIT_MAX_REQUESTS 1000   /* Max requests per window */
#define RATE_LIMIT_WINDOW_SECONDS 60   /* Window duration in seconds */

/* ------------------------------------------------------------------ */
/* Rate limiting: token bucket per UID                                 */
/* ------------------------------------------------------------------ */
typedef struct {
    uid_t uid;
    int   tokens;
    time_t window_start;
} rate_limit_entry;

/* Simple rate limiter: track requests per UID */
static rate_limit_entry rate_limit_table[64] = {0};
static int rate_limit_count = 0;

/* Guards rate_limit_table / rate_limit_count.
 *
 * check_rate_limit() is reached from verify_socket_credentials(), which runs
 * on a detached thread per accepted connection, so the lookup below is a
 * shared read-modify-write.  Without this lock, `rate_limit_count++` can lose
 * an update: two threads read the same value, both pass the bounds check, and
 * both write the same slot while the counter advances only once.  That leaks
 * a UID from the table and skews the fill count.
 */
static pthread_mutex_t rate_limit_lock = PTHREAD_MUTEX_INITIALIZER;

/* ------------------------------------------------------------------ */
/* Test seam                                                          */
/*                                                                     */
/* check_rate_limit() is static, so unit tests cannot reach it. These
 * wrappers expose it for tests/test_server_rate_limit.c without changing
 * production behaviour: they compile only under LMUX_TEST_RATE_LIMIT. */
/* ------------------------------------------------------------------ */
#ifdef LMUX_TEST_RATE_LIMIT

static int check_rate_limit(uid_t uid); /* defined below; forward decl for the seam */

int lmux_test_check_rate_limit(uid_t uid) {
    return check_rate_limit(uid);
}

int lmux_test_rate_limit_count(void) {
    return rate_limit_count;
}

int lmux_test_rate_limit_capacity(void) {
    return (int)(sizeof rate_limit_table / sizeof rate_limit_table[0]);
}

/* Reset the table so each test starts from a known state. */
void lmux_test_rate_limit_reset(void) {
    memset(rate_limit_table, 0, sizeof rate_limit_table);
    rate_limit_count = 0;
}

/* Count slots that actually hold an entry, independent of the shared
 * index.  If the index and the contents disagree, an increment was lost. */
int lmux_test_rate_limit_occupied(void) {
    int n = 0;
    for (size_t i = 0; i < sizeof rate_limit_table / sizeof rate_limit_table[0]; i++)
        if (rate_limit_table[i].uid != 0)
            n++;
    return n;
}

#endif /* LMUX_TEST_RATE_LIMIT */

/**
 * Check if a request from the given UID is allowed under rate limits.
 * Uses a sliding window counter approach.
 * 
 * @param uid  The UID of the requesting process.
 * @return 0 if allowed, -1 if rate limit exceeded.
 */
static int check_rate_limit(uid_t uid) {
    time_t now = time(NULL);
    int result = -1;   /* fail closed unless a path allows the request */

    pthread_mutex_lock(&rate_limit_lock);

    /* Find existing entry or create new one */
    for (int i = 0; i < rate_limit_count; i++) {
        if (rate_limit_table[i].uid == uid) {
            /* Reset window if expired */
            if (now - rate_limit_table[i].window_start >= RATE_LIMIT_WINDOW_SECONDS) {
                rate_limit_table[i].tokens = RATE_LIMIT_MAX_REQUESTS - 1;
                rate_limit_table[i].window_start = now;
                result = 0;
            } else if (rate_limit_table[i].tokens > 0) {
                rate_limit_table[i].tokens--;
                result = 0;
            } /* else: exhausted, keep result = -1 */
            goto done;
        }
    }

    /* Create new entry if table not full */
    if (rate_limit_count < 64) {
        rate_limit_table[rate_limit_count].uid = uid;
        rate_limit_table[rate_limit_count].tokens = RATE_LIMIT_MAX_REQUESTS - 1;
        rate_limit_table[rate_limit_count].window_start = now;
        rate_limit_count++;
        result = 0;
        goto done;
    }

    /* Table full, deny to prevent untracked abuse (fail closed) */
    lmux_log(LMUX_LOG_WARN, "server: rate limit table full, denying UID %u", uid);
    result = -1;

done:
    pthread_mutex_unlock(&rate_limit_lock);
    return result;
}

/* ------------------------------------------------------------------ */
/* Socket authentication: verify client credentials                    */
/* ------------------------------------------------------------------ */
/**
 * Verify that the connected client has the same UID as the server owner.
 * This prevents other users on the same system from accessing the socket.
 * 
 * @param client_fd  File descriptor of the connected client socket.
 * @return 0 if authentication succeeds, -1 if auth fails, -2 if rate limited.
 */
static int verify_socket_credentials(int client_fd, const char *socket_path) {
    struct ucred cred;
    socklen_t len = sizeof(cred);
    
    if (getsockopt(client_fd, SOL_SOCKET, SO_PEERCRED, &cred, &len) < 0) {
        lmux_log(LMUX_LOG_ERROR, "server: getsockopt(SO_PEERCRED) failed: %s", strerror(errno));
        return -1;
    }
    
    /* Get the UID of the socket file owner */
    struct stat st;
    if (stat(socket_path, &st) < 0) {
        lmux_log(LMUX_LOG_ERROR, "server: stat(%s) failed: %s", socket_path, strerror(errno));
        return -1;
    }
    
    uid_t socket_owner_uid = st.st_uid;
    
    lmux_log(LMUX_LOG_DEBUG, "server: client UID=%u, socket owner UID=%u", cred.uid, socket_owner_uid);
    
    if (cred.uid != socket_owner_uid) {
        lmux_log(LMUX_LOG_WARN, "server: rejected connection from UID %u (expected %u)", 
                 cred.uid, socket_owner_uid);
        return -1;
    }
    
    /* Check rate limit for this UID */
    if (check_rate_limit(cred.uid) < 0) {
        lmux_log(LMUX_LOG_WARN, "server: rate limit exceeded for UID %u", cred.uid);
        return -2;
    }
    
    return 0;
}

/* ------------------------------------------------------------------ */
/* Internal: accept and serve one client connection.                   */
/* ------------------------------------------------------------------ */
static void serve_client(lmux_app *app, int client_fd) {
    /* Track connection */
    lmux_metrics_inc_connections(app);

    /* Authenticate the client before processing any request */
    const char *socket_path = lmux_socket_path(app);
    int auth_rc = verify_socket_credentials(client_fd, socket_path);
    if (auth_rc < 0) {
        lmux_metrics_inc_errors(app);
        const char *err;
        if (auth_rc == -2) {
            err = "{\"ok\":false,\"error\":{\"type\":\"urn:lmux:rate-limited\",\"title\":\"Rate Limited\",\"code\":\"rate_limited\",\"detail\":\"rate limit exceeded, try again later\",\"trace_id\":\"null\"}}\n";
        } else {
            err = "{\"ok\":false,\"error\":{\"type\":\"urn:lmux:auth-failed\",\"title\":\"Authentication Failed\",\"code\":\"auth_failed\",\"detail\":\"authentication failed: UID mismatch\",\"trace_id\":\"null\"}}\n";
        }
        ssize_t _w = write(client_fd, err, strlen(err)); (void)_w;
        close(client_fd);
        return;
    }
    
    char buf[LMUX_MAX_REQUEST];
    size_t total = 0;
    bool got_newline = false;
    /* Loop reads until newline found or buffer full */
    while (total < sizeof buf - 1 && !got_newline) {
        struct pollfd pfd = {.fd = client_fd, .events = POLLIN, .revents = 0};
        int pr = poll(&pfd, 1, 5000);  /* 5s timeout */
        if (pr <= 0) break;
        ssize_t nr = read(client_fd, buf + total, sizeof buf - 1 - total);
        if (nr <= 0) break;
        for (ssize_t i = 0; i < nr; i++) {
            if (buf[total + i] == '\n') {
                got_newline = true;
                total += (size_t)i;
                break;
            }
        }
        if (!got_newline) total += (size_t)nr;
    }
    if (total == 0) {
        close(client_fd);
        return;
    }
    buf[total] = 0;

    /* Strip trailing newline(s). */
    while (total > 0 && (buf[total - 1] == '\n' || buf[total - 1] == '\r')) {
        buf[--total] = 0;
    }

    /* Basic request validation: must start with '{' (JSON object). */
    const char *p = buf;
    while (*p == ' ' || *p == '\t') p++;
    if (*p != '{') {
        lmux_metrics_inc_errors(app);
        const char *err = "{\"ok\":false,\"error\":{\"type\":\"urn:lmux:invalid-request\",\"title\":\"Invalid Request\",\"code\":\"invalid_request\",\"detail\":\"request must be a JSON object\",\"trace_id\":\"null\"}}\n";
        ssize_t _w = write(client_fd, err, strlen(err)); (void)_w;
        close(client_fd);
        return;
    }

    /* Dispatch. */
    char *response = lmux_dispatch_json(app, buf);
    lmux_metrics_inc_requests(app);

    /* Check for event streaming response. */
    int is_events = response && strstr(response, "\"stream\":\"events\"") != NULL;
    char name_filter[128] = {0}, cat_filter[128] = {0};
    if (is_events && response) {
        /* Extract filter values from the response JSON */
        const char *nf = strstr(response, "\"name_filter\":\"");
        if (nf) {
            nf += 15;
            size_t k = 0;
            while (*nf && *nf != '"' && k < sizeof name_filter - 1)
                name_filter[k++] = *nf++;
        }
        const char *cf = strstr(response, "\"category_filter\":\"");
        if (cf) {
            cf += 19;
            size_t k = 0;
            while (*cf && *cf != '"' && k < sizeof cat_filter - 1)
                cat_filter[k++] = *cf++;
        }
    }

    /* Write response + newline. */
    if (response) {
        size_t rlen = strlen(response);
        ssize_t ignored;
        ignored = write(client_fd, response, rlen);
        ignored = write(client_fd, "\n", 1);
        (void)ignored;

        /* Signal EOF to client so recv() returns 0 and the client's
         * read loop terminates immediately instead of waiting for
         * close() to propagate through the kernel.  Skip this for
         * event-streaming connections which must stay open. */
        if (!is_events) {
            shutdown(client_fd, SHUT_WR);
        }

        /* If events command, keep connection open and stream events. */
        if (is_events) {
            free(response);
            response = NULL;

            /* Send ack frame */
            char ack[2048];
            snprintf(ack, sizeof ack,
                "{\"type\":\"ack\",\"protocol\":\"lmux-events\",\"version\":1,"
                "\"boot_id\":\"%s\",\"heartbeat_interval_seconds\":15,"
                "\"latest_seq\":%llu}\n",
                lmux_app_boot_id(app), (unsigned long long)lmux_app_event_seq(app));
            { ssize_t _w = write(client_fd, ack, strlen(ack)); (void)_w; }

            /* Set non-blocking */
            int flags = fcntl(client_fd, F_GETFL, 0);
            fcntl(client_fd, F_SETFL, flags | O_NONBLOCK);

            time_t last_heartbeat = time(NULL);

            while (lmux_app_is_running(app)) {
                struct pollfd pfds[1] = {{.fd = client_fd, .events = POLLIN}};
                int pr = poll(pfds, 1, 100);
                if (pr < 0) break;

                /* Check client disconnect */
                char discard[64];
                ssize_t rb = read(client_fd, discard, sizeof discard);
                if (rb <= 0 && pr > 0) break;

                /* Send pending events */
                for (;;) {
                    char *ev = lmux_event_pop(app);
                    if (!ev) break;
                    /* Check filter before sending */
                    if ((name_filter[0] || cat_filter[0])) {
                        int matches = 1;
                        if (name_filter[0]) {
                            char search_name[256];
                            snprintf(search_name, sizeof search_name, "\"name\":\"%s\"", name_filter);
                            if (!strstr(ev, search_name)) matches = 0;
                        }
                        if (cat_filter[0] && matches) {
                            char search_cat[256];
                            snprintf(search_cat, sizeof search_cat, "\"category\":\"%s\"", cat_filter);
                            if (!strstr(ev, search_cat)) matches = 0;
                        }
                        if (!matches) { free(ev); continue; }
                    }
                    { ssize_t _w = write(client_fd, ev, strlen(ev)); (void)_w; }
                    { ssize_t _w = write(client_fd, "\n", 1); (void)_w; }
                    free(ev);
                }

                /* Send heartbeat */
                time_t now = time(NULL);
                if (now - last_heartbeat >= 15) {
                    char hb[1024];
                    snprintf(hb, sizeof hb,
                        "{\"type\":\"heartbeat\",\"latest_seq\":%llu,\"boot_id\":\"%s\"}\n",
                        (unsigned long long)lmux_app_event_seq(app), lmux_app_boot_id(app));
                    { ssize_t _w = write(client_fd, hb, strlen(hb)); (void)_w; }
                    last_heartbeat = now;
                }
            }

            /* Restore blocking mode (best-effort). */
            fcntl(client_fd, F_SETFL, flags);
        } else {
            free(response);
            response = NULL;
        }
    }

    close(client_fd);
}

/* ------------------------------------------------------------------ */
/* Threaded accept loop (for CLI/headless mode).                       */
/* ------------------------------------------------------------------ */

/* Thread argument for per-client handler. */
typedef struct {
    lmux_app *app;
    int       client_fd;
} client_thread_arg;

/* Thread argument for the accept loop thread. */
typedef struct {
    lmux_app *app;
    int       listen_fd;
} server_thread_arg;

static void *client_thread(void *arg) {
    client_thread_arg *cta = (client_thread_arg *)arg;
    serve_client(cta->app, cta->client_fd);
    free(cta);
    return NULL;
}

static void *server_thread(void *arg) {
    server_thread_arg *sta = (server_thread_arg *)arg;
    lmux_app *app = sta->app;
    int listen_fd = sta->listen_fd;

    free(sta);

    while (1) {
        struct sockaddr_un client_addr;
        socklen_t client_len = sizeof client_addr;
        int client_fd = accept(listen_fd, (struct sockaddr *)&client_addr,
                               &client_len);
        if (client_fd < 0) {
            if (errno == EINTR) continue;
            break;
        }
        /* Set FD_CLOEXEC so the client socket fd is not inherited by forked children */
        fcntl(client_fd, F_SETFD, FD_CLOEXEC);

        /* Spawn a thread to handle this client so accept loop stays free. */
        client_thread_arg *cta = malloc(sizeof *cta);
        if (cta) {
            cta->app = app;
            cta->client_fd = client_fd;
            pthread_t th;
            if (pthread_create(&th, NULL, client_thread, cta) == 0) {
                pthread_detach(th);
            } else {
                free(cta);
                close(client_fd);
            }
        } else {
            close(client_fd);
        }
    }

    return NULL;
}

/* ------------------------------------------------------------------ */
/* Public API                                                          */
/* ------------------------------------------------------------------ */

int lmux_server_start(lmux_app *app) {
    if (!app) return -1;

    const char *socket_path = lmux_socket_path(app);
    if (!socket_path || !socket_path[0]) return -1;

    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        lmux_log(LMUX_LOG_ERROR, "server: socket() failed: %s", strerror(errno));
        return -1;
    }
    /* Set FD_CLOEXEC so the listening socket fd is not inherited by forked children */
    fcntl(fd, F_SETFD, FD_CLOEXEC);

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof addr);
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, socket_path, sizeof addr.sun_path - 1);

    /* Try bind first to avoid TOCTOU race between unlink and bind. */
    if (bind(fd, (struct sockaddr *)&addr, sizeof addr) < 0) {
        if (errno == EADDRINUSE) {
            /* Socket exists — check if it's a stale orphan by connecting. */
            int probe = socket(AF_UNIX, SOCK_STREAM, 0);
            if (probe >= 0) {
                int rc = connect(probe, (struct sockaddr *)&addr, sizeof addr);
                close(probe);
                if (rc == 0) {
                    /* Active daemon owns this socket — refuse to overwrite. */
                    lmux_log(LMUX_LOG_ERROR, "server: daemon already running on %s", socket_path);
                    close(fd);
                    return -1;
                }
            }
            /* Stale socket (no active daemon) — safe to remove and retry. */
            lmux_log(LMUX_LOG_WARN, "server: removing stale socket %s", socket_path);
            unlink(socket_path);
            if (bind(fd, (struct sockaddr *)&addr, sizeof addr) < 0) {
                lmux_log(LMUX_LOG_ERROR, "server: bind(%s) failed after stale removal: %s",
                         socket_path, strerror(errno));
                close(fd);
                return -1;
            }
        } else {
            lmux_log(LMUX_LOG_ERROR, "server: bind(%s) failed: %s",
                     socket_path, strerror(errno));
            close(fd);
            return -1;
        }
    }

    /* Restrict socket to owner-only. */
    chmod(socket_path, 0600);

    if (listen(fd, 5) < 0) {
        lmux_log(LMUX_LOG_ERROR, "server: listen() failed: %s", strerror(errno));
        close(fd);
        unlink(socket_path);
        return -1;
    }

    lmux_log(LMUX_LOG_INFO, "server: listening on %s", socket_path);
    lmux_server_set_listen_fd(app, fd);
    return 0;
}

int lmux_server_start_threaded(lmux_app *app) {
    int rc = lmux_server_start(app);
    if (rc != 0) return rc;

    int fd = lmux_server_get_listen_fd(app);
    if (fd < 0) return -1;

    server_thread_arg *arg = calloc(1, sizeof *arg);
    if (!arg) {
        lmux_log(LMUX_LOG_ERROR, "server: out of memory");
        close(fd);
        lmux_server_set_listen_fd(app, -1);
        unlink(lmux_socket_path(app));
        return -1;
    }
    arg->app = app;
    arg->listen_fd = fd;

    pthread_t tid;
    if (pthread_create(&tid, NULL, server_thread, arg) != 0) {
        lmux_log(LMUX_LOG_ERROR, "server: pthread_create failed: %s", strerror(errno));
        free(arg);
        close(fd);
        lmux_server_set_listen_fd(app, -1);
        unlink(lmux_socket_path(app));
        return -1;
    }

    return 0;
}

/* Non-blocking tick: accept one pending connection if any. */
int lmux_server_tick(lmux_app *app) {
    if (!app) return 0;
    int fd = lmux_server_get_listen_fd(app);
    if (fd < 0) return 0;

    struct sockaddr_un client_addr;
    socklen_t client_len = sizeof client_addr;

    /* Set non-blocking for the tick. */
    int flags = fcntl(fd, F_GETFL, 0);
    fcntl(fd, F_SETFL, flags | O_NONBLOCK);

    int client_fd = accept(fd, (struct sockaddr *)&client_addr,
                           &client_len);
    if (client_fd >= 0) {
        serve_client(app, client_fd);
    }

    /* Restore blocking. */
    fcntl(fd, F_SETFL, flags);
    return client_fd >= 0 ? 1 : 0;
}

void lmux_server_stop(lmux_app *app) {
    if (!app) return;
    int fd = lmux_server_get_listen_fd(app);
    if (fd >= 0) {
        const char *path = lmux_socket_path(app);
        close(fd);
        lmux_server_set_listen_fd(app, -1);
        if (path) unlink(path);
        lmux_log(LMUX_LOG_INFO, "server: stopped");
    }
}
