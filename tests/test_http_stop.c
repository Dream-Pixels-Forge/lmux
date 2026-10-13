/**
 * test_http_stop.c — ISS-0011: HTTP server stop vs worker close race.
 *
 * stop() used to close() every client_fds[] slot while detached workers
 * could concurrently close() the same fd at their `done:` label
 * (close/close race + fd reuse), and it freed the server object while
 * long-lived SSE workers still dereferenced it (use-after-free). Worse,
 * stop() joined the accept thread while it sat blocked in accept(), so
 * stop() itself could hang forever (close() does not wake accept()).
 *
 * RED: start the server, hold an SSE stream + short connections open,
 * run stop() on a helper thread, and require it to return within a
 * bounded wait. Pre-fix it never returns (join on the stuck accept
 * thread) and the test fails via timeout.
 *
 * GREEN: stop() wakes accept() via shutdown(), wakes workers via
 * shutdown() (leaving close() ownership to the worker), drains worker
 * slots with a bounded wait, and only then frees the server. The port
 * must also be immediately reusable (listen fd really closed).
 *
 * Uses ONLY public API + POSIX sockets. No internal struct access.
 */
#define _POSIX_C_SOURCE 200809L /* nanosleep */
#include "lmux.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <errno.h>
#include <time.h>
#include <pthread.h>
#include <fcntl.h>
#include <poll.h>
#include <arpa/inet.h>
#include <signal.h>
#include <sys/socket.h>
#include <netinet/in.h>

static int tests_run = 0;
static int tests_pass = 0;
static int tests_fail = 0;

#define TEST(name) do { \
    tests_run++; \
    printf("  TEST %s ... ", name); \
    fflush(stdout); \
} while (0)

#define PASS() do { \
    tests_pass++; \
    printf("PASS\n"); \
} while (0)

#define FAIL(fmt, ...) do { \
    tests_fail++; \
    printf("FAIL: " fmt "\n", ##__VA_ARGS__); \
} while (0)

#define ASSERT(cond, msg) do { \
    if (!(cond)) { FAIL("%s (line %d)", msg, __LINE__); return; } \
} while (0)

static int g_port = 0;
static volatile int g_stop_done = 0;
static volatile int g_keep_clients = 1;

static int dial(void) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof addr);
    addr.sin_family = AF_INET;
    addr.sin_port = htons((uint16_t)g_port);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (connect(fd, (struct sockaddr *)&addr, sizeof addr) < 0) {
        close(fd);
        return -1;
    }
    return fd;
}

/* One short /api/logs request; returns 1 on HTTP 200 with a body. */
static int short_request(void) {
    int fd = dial();
    if (fd < 0) return 0;
    const char *req = "GET /api/logs HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n";
    ssize_t w = write(fd, req, strlen(req));
    if (w != (ssize_t)strlen(req)) { close(fd); return 0; }
    char buf[65536];
    size_t n = 0;
    ssize_t r;
    while ((r = read(fd, buf + n, sizeof buf - n - 1)) > 0) {
        n += (size_t)r;
        if (n >= sizeof buf - 1) break;
    }
    close(fd);
    if (n == 0) return 0;
    buf[n] = 0;
    return strstr(buf, "200") != NULL;
}

/* Short-lived clients hammering the server while stop() runs. */
static void *short_client_thread(void *arg) {
    (void)arg;
    while (g_keep_clients) {
        short_request();
        struct timespec ts = {0, 20 * 1000 * 1000L};
        nanosleep(&ts, NULL);
    }
    return NULL;
}

/* Long-lived SSE stream holder: reads headers, then holds the socket
 * open until the server goes away (this is the worker stop() must not
 * double-close or use-after-free). */
static void *sse_client_thread(void *arg) {
    (void)arg;
    int fd = dial();
    if (fd < 0) return NULL;
    const char *req = "GET /api/logs/stream HTTP/1.1\r\nHost: x\r\n\r\n";
    if (write(fd, req, strlen(req)) != (ssize_t)strlen(req)) {
        close(fd);
        return NULL;
    }
    /* Read until end of SSE headers. */
    char buf[4096];
    size_t n = 0;
    ssize_t r;
    int got_headers = 0;
    while ((r = read(fd, buf + n, sizeof buf - n - 1)) > 0) {
        n += (size_t)r;
        buf[n] = 0;
        if (strstr(buf, "\r\n\r\n")) { got_headers = 1; break; }
        if (n >= sizeof buf - 1) break;
    }
    (void)got_headers;
    /* Hold open until the server closes/shuts down, with a hard cap so
     * a wedged server cannot hang the test suite forever. */
    struct pollfd pfd = {.fd = fd, .events = POLLIN, .revents = 0};
    time_t deadline = time(NULL) + 25;
    while (time(NULL) < deadline) {
        int pr = poll(&pfd, 1, 200);
        if (pr > 0) {
            char tmp[512];
            if (read(fd, tmp, sizeof tmp) <= 0) break;
        } else if (pr < 0) {
            break;
        }
        if (!g_keep_clients) {
            /* Server is stopping; keep holding briefly so the worker
             * is still alive when stop() runs, then let go. */
            struct timespec ts = {0, 300 * 1000 * 1000L};
            nanosleep(&ts, NULL);
            break;
        }
    }
    close(fd);
    return NULL;
}

static void *stopper_thread(void *arg) {
    (void)arg;
    lmux_http_server_stop();
    g_stop_done = 1;
    return NULL;
}

static int start_server_on_scratch_port(lmux_app *app) {
    for (int p = 18080; p < 18088; p++) {
        if (lmux_http_server_start(app, p) == 0) {
            g_port = p;
            return 0;
        }
    }
    return -1;
}

static void test_stop_returns_with_live_workers(void) {
    TEST("stop returns promptly with concurrent + SSE connections open");
    char sock[128];
    snprintf(sock, sizeof sock, "/tmp/lmux-http-test-%d.sock", getpid());
    unlink(sock);
    lmux_app *app = lmux_app_new(sock);
    ASSERT(app != NULL, "lmux_app_new should succeed");
    ASSERT(start_server_on_scratch_port(app) == 0,
           "http server should start on a scratch port");

    /* Warm up: one request must succeed before the race window. */
    ASSERT(short_request(), "warmup /api/logs request should return 200");

    g_keep_clients = 1;
    g_stop_done = 0;
    pthread_t shorts[8], sse, stopper;
    for (int i = 0; i < 8; i++)
        pthread_create(&shorts[i], NULL, short_client_thread, NULL);
    pthread_create(&sse, NULL, sse_client_thread, NULL);

    /* Let workers spin up, then stop while they are live. */
    struct timespec ts = {0, 800 * 1000 * 1000L};
    nanosleep(&ts, NULL);
    g_keep_clients = 0;
    pthread_create(&stopper, NULL, stopper_thread, NULL);

    /* Bounded wait: stop() must return (pre-fix it hangs in
     * pthread_join on the accept thread stuck in accept()). */
    int waited_ms = 0;
    while (!g_stop_done && waited_ms < 12000) {
        struct timespec tick = {0, 50 * 1000 * 1000L};
        nanosleep(&tick, NULL);
        waited_ms += 50;
    }
    if (!g_stop_done) {
        FAIL("lmux_http_server_stop() did not return within 12s "
             "(accept-thread join hang / worker race) (line %d)", __LINE__);
        /* Do not join the stuck stopper: kill the process so the
         * suite stays bounded. Workers die with it. */
        fflush(stdout);
        _exit(1);
    }
    pthread_join(stopper, NULL);
    for (int i = 0; i < 8; i++) pthread_join(shorts[i], NULL);
    pthread_join(sse, NULL);

    /* The listen socket must really be gone: the same port restarts. */
    ASSERT(lmux_http_server_start(app, g_port) == 0,
           "server should restart on the same port after stop");
    ASSERT(short_request(), "request after restart should return 200");
    lmux_http_server_stop();

    lmux_app_free(app);
    unlink(sock);
    PASS();
}

int main(void) {
    /* Mirror the daemon runtime: daemon mode installs SIG_IGN for
     * SIGPIPE before starting the HTTP server, so worker writes to
     * raced-away sockets return EPIPE instead of killing the process. */
    signal(SIGPIPE, SIG_IGN);
    printf("test_http_stop (ISS-0011)\n");
    test_stop_returns_with_live_workers();
    printf("%d run, %d pass, %d fail\n", tests_run, tests_pass, tests_fail);
    return tests_fail ? 1 : 0;
}
