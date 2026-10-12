/* HTTP Server for SSE log streaming */
#include "lmux.h"
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <poll.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <fcntl.h>
#include <errno.h>

#define HTTP_MAX_CLIENTS 32
#define HTTP_BUFFER_SIZE 8192

typedef struct {
    lmux_app *app;
    int listen_fd;
    int port;
    pthread_t thread_id;
    volatile bool running;
    int client_fds[HTTP_MAX_CLIENTS];
    pthread_mutex_t clients_lock;
} http_server_t;

static http_server_t *g_http_server = NULL;

static const char *http_get_mime_type(const char *path) {
    if (strstr(path, ".html")) return "text/html";
    if (strstr(path, ".css")) return "text/css";
    if (strstr(path, ".js")) return "application/javascript";
    if (strstr(path, ".json")) return "application/json";
    if (strstr(path, ".svg")) return "image/svg+xml";
    if (strstr(path, ".ico")) return "image/x-icon";
    return "text/plain";
}

/* Escape a string for safe inclusion in a JSON string value (ISS-0002:
 * log lines are untrusted input — a quote, backslash or control
 * character in any line must not be able to break the /api/logs
 * response or inject JSON). Mirrors log_json_escape in model.c. */
static size_t http_json_escape(const char *in, char *out, size_t cap) {
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

static void http_send_response(int client_fd, int status, const char *content_type, const char *body) {
    char header[1024];
    int body_len = body ? strlen(body) : 0;
    snprintf(header, sizeof header,
        "HTTP/1.1 %d %s\r\n"
        "Content-Type: %s\r\n"
        "Content-Length: %d\r\n"
        "Connection: close\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "\r\n",
        status,
        status == 200 ? "OK" : (status == 404 ? "Not Found" : "Internal Server Error"),
        content_type, body_len);
    write(client_fd, header, strlen(header));
    if (body) write(client_fd, body, body_len);
}

static void http_send_sse_header(int client_fd) {
    const char *header =
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/event-stream\r\n"
        "Cache-Control: no-cache\r\n"
        "Connection: keep-alive\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "\r\n";
    write(client_fd, header, strlen(header));
}

static void http_send_sse_event(int client_fd, const char *data) {
    char buf[HTTP_BUFFER_SIZE];
    int len = snprintf(buf, sizeof buf, "data: %s\n\n", data);
    write(client_fd, buf, len);
}

static void *http_client_thread(void *arg) {
    int client_fd = *(int *)arg;
    free(arg);

    char buffer[HTTP_BUFFER_SIZE];
    ssize_t bytes_read = read(client_fd, buffer, sizeof(buffer) - 1);
    if (bytes_read <= 0) {
        close(client_fd);
        return NULL;
    }
    buffer[bytes_read] = '\0';

    /* Parse request line */
    char method[16], path[256], version[16];
    if (sscanf(buffer, "%15s %255s %15s", method, path, version) != 3) {
        http_send_response(client_fd, 400, "text/plain", "Bad Request");
        close(client_fd);
        return NULL;
    }

    /* Handle /api/logs/stream endpoint */
    extern http_server_t *g_http_server;
    if (strcmp(path, "/api/logs/stream") == 0 || strncmp(path, "/api/logs/stream?", 17) == 0) {
        if (!g_http_server || !g_http_server->app) {
            http_send_response(client_fd, 500, "text/plain", "Server not initialized");
            close(client_fd);
            return NULL;
        }

        lmux_app *app = g_http_server->app;
        const char *log_path = lmux_app_get_log_file(app);

        /* Send SSE headers */
        extern void http_send_sse_header(int client_fd);
        http_send_sse_header(client_fd);

        /* Send initial keepalive */
        write(client_fd, ": keepalive\n\n", 13);

        /* If log file exists, send existing lines first */
        if (log_path && log_path[0]) {
            FILE *fp = fopen(log_path, "r");
            if (fp) {
                char line_buf[4096];
                while (fgets(line_buf, sizeof line_buf, fp)) {
                    size_t len = strlen(line_buf);
                    if (len > 0 && line_buf[len - 1] == '\n') line_buf[len - 1] = '\0';
                    extern void http_send_sse_event(int client_fd, const char *data);
                    http_send_sse_event(client_fd, line_buf);
                }
                fclose(fp);
            }
        }

        /* Tail the log file for new lines */
        if (log_path && log_path[0]) {
            FILE *fp = fopen(log_path, "r");
            if (fp) {
                /* Seek to end */
                fseek(fp, 0, SEEK_END);
                long last_pos = ftell(fp);

                extern http_server_t *g_http_server;
                while (g_http_server && g_http_server->running) {
                    /* Check for new data */
                    fseek(fp, 0, SEEK_END);
                    long cur_pos = ftell(fp);
                    if (cur_pos > last_pos) {
                        fseek(fp, last_pos, SEEK_SET);
                        char line_buf[4096];
                        while (fgets(line_buf, sizeof line_buf, fp)) {
                            size_t len = strlen(line_buf);
                            if (len > 0 && line_buf[len - 1] == '\n') line_buf[len - 1] = '\0';
                            extern void http_send_sse_event(int client_fd, const char *data);
                            http_send_sse_event(client_fd, line_buf);
                        }
                        last_pos = ftell(fp);
                    } else if (cur_pos < last_pos) {
                        /* Log rotation detected - reopen */
                        fclose(fp);
                        fp = fopen(log_path, "r");
                        if (fp) {
                            fseek(fp, 0, SEEK_END);
                            last_pos = ftell(fp);
                        } else {
                            break;
                        }
                    }

                    /* Send heartbeat every 15 seconds */
                    static time_t last_heartbeat = 0;
                    time_t now = time(NULL);
                    if (now - last_heartbeat >= 15) {
                        write(client_fd, ": ping\n\n", 8);
                        last_heartbeat = now;
                    }

                    /* Check if client disconnected */
                    struct pollfd pfd;
                    pfd.fd = client_fd;
                    pfd.events = POLLHUP | POLLERR | POLLNVAL;
                    if (poll(&pfd, 1, 1000) > 0) {
                        break;
                    }
                }
                if (fp) fclose(fp);
            }
        }

        close(client_fd);
        return NULL;
    }
    /* Handle other endpoints */
    else if (strcmp(path, "/") == 0 || strcmp(path, "/index.html") == 0) {
        http_send_response(client_fd, 200, "text/html",
            "<html><body><h1>lmux HTTP Server</h1><p>Try <a href=\"/api/logs/stream\">/api/logs/stream</a> for SSE log streaming.</p></body></html>");
    } else if (strcmp(path, "/api/logs") == 0) {
        extern http_server_t *g_http_server;
        if (!g_http_server || !g_http_server->app) {
            http_send_response(client_fd, 500, "text/plain", "Server not initialized");
        } else {
            lmux_app *app = g_http_server->app;
            const char *log_path = lmux_app_get_log_file(app);
            if (!log_path || !log_path[0]) {
                http_send_response(client_fd, 200, "application/json", "{\"lines\":[],\"message\":\"no log file configured\"}");
            } else {
                FILE *fp = fopen(log_path, "r");
                if (!fp) {
                    http_send_response(client_fd, 200, "application/json", "{\"lines\":[],\"message\":\"log file not found\"}");
                } else {
                    char line_buf[4096];
                    char *json = malloc(65536);
                    size_t json_len = snprintf(json, 65536, "{\"lines\":[");
                    int count = 0;
                    while (fgets(line_buf, sizeof line_buf, fp) && count < 100) {
                        size_t len = strlen(line_buf);
                        if (len > 0 && line_buf[len - 1] == '\n') line_buf[len - 1] = '\0';
                        if (count > 0) json_len += snprintf(json + json_len, 65536 - json_len, ",");
                        char escaped[8192];
                        http_json_escape(line_buf, escaped, sizeof escaped);
                        json_len += snprintf(json + json_len, 65536 - json_len, "\"%s\"", escaped);
                        count++;
                    }
                    fclose(fp);
                    json_len += snprintf(json + json_len, 65536 - json_len, "],\"count\":%d}", count);
                    if (json_len < 65536) {
                        http_send_response(client_fd, 200, "application/json", json);
                    } else {
                        http_send_response(client_fd, 200, "application/json", "{\"ok\":false,\"error\":{\"code\":\"response_too_large\"}}");
                    }
                    free(json);
            }
            }
        }
        } else {
            http_send_response(client_fd, 404, "text/plain", "Not Found");
        }

        close(client_fd);
        return NULL;
    }

    static void *http_server_thread(void *arg)
    {
        http_server_t *server = (http_server_t *)arg;
        struct sockaddr_in client_addr;
        socklen_t client_len = sizeof(client_addr);

        while (server->running) {
            int client_fd = accept(server->listen_fd, (struct sockaddr *)&client_addr, &client_len);
            if (client_fd < 0) {
                if (!server->running) break;
                continue;
            }

            pthread_mutex_lock(&server->clients_lock);
            int slot = -1;
            for (int i = 0; i < HTTP_MAX_CLIENTS; i++) {
                if (server->client_fds[i] < 0) {
                    slot = i;
                    server->client_fds[i] = client_fd;
                    break;
                }
            }
            pthread_mutex_unlock(&server->clients_lock);

            if (slot == -1) {
                close(client_fd);
                continue;
            }

            int *client_fd_ptr = malloc(sizeof(int));
            *client_fd_ptr = client_fd;
            pthread_t tid;
            pthread_create(&tid, NULL, http_client_thread, client_fd_ptr);
            pthread_detach(tid);
        }
        return NULL;
    }

    int lmux_http_server_start(lmux_app *app, int port)
    {
        if (g_http_server) {
            return -1; /* Already running */
        }

        http_server_t *server = calloc(1, sizeof(http_server_t));
        if (!server) {
            return -1;
        }

        server->app = app;
        server->port = port;
        server->running = true;
        server->listen_fd = -1;
        /* calloc() zeroes client_fds, but 0 is a valid fd number — the
         * accept loop looks for slots < 0, so without this every
         * connection is dropped (and stop() would close fd 0). */
        for (int i = 0; i < HTTP_MAX_CLIENTS; i++) {
            server->client_fds[i] = -1;
        }

        int listen_fd = socket(AF_INET, SOCK_STREAM, 0);
        if (listen_fd < 0) {
            free(server);
            return -1;
        }

        int opt = 1;
        setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

        struct sockaddr_in addr = {0};
        addr.sin_family = AF_INET;
        addr.sin_port = htons(port);
        /* ISS-0002: bind the loopback interface (127.0.0.1) only. The
         * daemon's Unix socket already assumes local-only clients
         * (SO_PEERCRED UID check); exposing the log stream on every
         * interface with no auth would leak logs to the whole network. */
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);

        if (bind(listen_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
            close(listen_fd);
            free(server);
            return -1;
        }

        if (listen(listen_fd, 10) < 0) {
            close(listen_fd);
            free(server);
            return -1;
        }

        server->listen_fd = listen_fd;

        pthread_mutex_init(&server->clients_lock, NULL);

        if (pthread_create(&server->thread_id, NULL, http_server_thread, server) != 0) {
            close(listen_fd);
            pthread_mutex_destroy(&server->clients_lock);
            free(server);
            return -1;
        }

        g_http_server = server;
        return 0;
    }

    void lmux_http_server_stop(void)
    {
        if (!g_http_server) {
            return;
        }

        g_http_server->running = false;

        if (g_http_server->listen_fd >= 0) {
            close(g_http_server->listen_fd);
            g_http_server->listen_fd = -1;
        }

        pthread_join(g_http_server->thread_id, NULL);

        pthread_mutex_lock(&g_http_server->clients_lock);
        for (int i = 0; i < HTTP_MAX_CLIENTS; i++) {
            if (g_http_server->client_fds[i] >= 0) {
                close(g_http_server->client_fds[i]);
                g_http_server->client_fds[i] = -1;
            }
        }
        pthread_mutex_unlock(&g_http_server->clients_lock);

        pthread_mutex_destroy(&g_http_server->clients_lock);
        free(g_http_server);
        g_http_server = NULL;
    }

    int lmux_http_server_start(lmux_app *app, int port);
    void lmux_http_server_stop(void);
