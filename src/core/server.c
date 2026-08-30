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

/* Maximum request body we accept (including newline). */
#define LMUX_MAX_REQUEST 65536

/* ------------------------------------------------------------------ */
/* Internal: accept and serve one client connection.                   */
/* ------------------------------------------------------------------ */
static void serve_client(lmux_app *app, int client_fd) {
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
        const char *err = "{\"ok\":false,\"error\":{\"code\":\"invalid_request\",\"message\":\"request must be a JSON object\"}}\n";
        ssize_t _w = write(client_fd, err, strlen(err)); (void)_w;
        close(client_fd);
        return;
    }

    /* Dispatch. */
    char *response = lmux_dispatch_json(app, buf);

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

    /* Remove any existing socket file. */
    unlink(socket_path);

    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        lmux_log(LMUX_LOG_ERROR, "server: socket() failed: %s", strerror(errno));
        return -1;
    }

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof addr);
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, socket_path, sizeof addr.sun_path - 1);

    if (bind(fd, (struct sockaddr *)&addr, sizeof addr) < 0) {
        lmux_log(LMUX_LOG_ERROR, "server: bind(%s) failed: %s",
                 socket_path, strerror(errno));
        close(fd);
        return -1;
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
