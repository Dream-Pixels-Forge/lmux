/**
 * test_model.c — Unit tests for lmux workspace/surface/pane model.
 *
 * Uses ONLY public API accessors. No internal struct access.
 */
#include "lmux.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/stat.h>

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

static char sock_path[128];

static lmux_app *make_app(void) {
    snprintf(sock_path, sizeof sock_path, "/tmp/lmux-test-%d.sock", getpid());
    /* Delete stale snapshot to ensure clean state for each test. */
    const char *home = getenv("HOME");
    if (home) {
        char snap[512];
        snprintf(snap, sizeof snap, "%s/.local/share/lmux/snapshot.json", home);
        unlink(snap);
        snprintf(snap, sizeof snap, "%s/.local/share/lmux/snapshot.json.bak", home);
        unlink(snap);
    }
    return lmux_app_new(sock_path);
}

static void teardown(void) { unlink(sock_path); }

/* Workspace lifecycle */
static void test_workspace_create(void) {
    TEST("workspace_create creates a workspace with title");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "test-ws");
    ASSERT(ws != NULL, "workspace_create should succeed");
    lmux_workspace_info info;
    ASSERT(lmux_workspace_get_info(ws, &info), "get_info should succeed");
    ASSERT(strcmp(info.title, "test-ws") == 0, "title should match");
    ASSERT(lmux_workspace_count(app) == 1, "count should be 1");
    lmux_app_free(app); teardown(); PASS();
}

static void test_workspace_by_id(void) {
    TEST("workspace_by_id returns correct workspace");
    lmux_app *app = make_app();
    lmux_workspace *ws1 = lmux_workspace_create(app, "alpha");
    lmux_workspace *ws2 = lmux_workspace_create(app, "beta");
    lmux_workspace_info i1, i2;
    lmux_workspace_get_info(ws1, &i1);
    lmux_workspace_get_info(ws2, &i2);
    lmux_id id1 = (lmux_id)atol(i1.id);
    lmux_id id2 = (lmux_id)atol(i2.id);
    ASSERT(lmux_workspace_by_id(app, id1) == ws1, "find ws1 by id");
    ASSERT(lmux_workspace_by_id(app, id2) == ws2, "find ws2 by id");
    ASSERT(lmux_workspace_by_id(app, 9999) == NULL, "unknown id returns NULL");
    lmux_app_free(app); teardown(); PASS();
}

static void test_workspace_by_index(void) {
    TEST("workspace_by_index returns workspaces in order");
    lmux_app *app = make_app();
    lmux_workspace *ws1 = lmux_workspace_create(app, "first");
    lmux_workspace *ws2 = lmux_workspace_create(app, "second");
    ASSERT(lmux_workspace_by_index(app, 0) == ws1, "index 0 is first");
    ASSERT(lmux_workspace_by_index(app, 1) == ws2, "index 1 is second");
    ASSERT(lmux_workspace_by_index(app, 2) == NULL, "out of bounds is NULL");
    lmux_app_free(app); teardown(); PASS();
}

static void test_workspace_close(void) {
    TEST("workspace_close removes workspace from app");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "to-close");
    lmux_workspace_info info;
    lmux_workspace_get_info(ws, &info);
    lmux_id id = (lmux_id)atol(info.id);
    lmux_workspace_close(app, ws);
    ASSERT(lmux_workspace_by_id(app, id) == NULL, "closed workspace not found");
    lmux_app_free(app); teardown(); PASS();
}

static void test_workspace_ssh_create(void) {
    TEST("workspace_ssh_create creates SSH workspace");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_ssh_create(app, "user@host.example.com", 2222);
    ASSERT(ws != NULL, "SSH workspace should be created");
    lmux_workspace_info info;
    ASSERT(lmux_workspace_get_info(ws, &info), "get_info should succeed");
    ASSERT(strlen(info.title) > 0, "SSH workspace should have a title");
    lmux_app_free(app); teardown(); PASS();
}

/* Surface lifecycle */
static void test_surface_create(void) {
    TEST("surface_create creates a surface in workspace");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "my-surface");
    ASSERT(surf != NULL, "surface_create should succeed");
    lmux_surface_info info;
    ASSERT(lmux_surface_get_info(surf, &info), "get_info should succeed");
    ASSERT(strcmp(info.title, "my-surface") == 0, "title should match");
    ASSERT(lmux_surface_count(ws) >= 1, "should have at least 1 surface");
    lmux_app_free(app); teardown(); PASS();
}

static void test_surface_focused(void) {
    TEST("surface_focused returns the focused surface");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface_create(ws, "one");
    lmux_surface *s2 = lmux_surface_create(ws, "two");
    /* surface_create always focuses the NEW surface */
    ASSERT(lmux_surface_focused(ws) == s2, "last created surface should be focused");
    lmux_app_free(app); teardown(); PASS();
}

/* Pane lifecycle */
static void test_pane_split(void) {
    TEST("pane_split creates a new pane alongside existing one");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    size_t before = lmux_pane_count(ws);
    lmux_pane *new_pane = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "echo test", false);
    ASSERT(new_pane != NULL, "split should succeed");
    ASSERT(lmux_pane_count(ws) == before + 1, "should have one more pane");
    lmux_app_free(app); teardown(); PASS();
}

static void test_pane_focused(void) {
    TEST("pane_focused returns the focused pane");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, NULL, false);
    ASSERT(lmux_pane_focused(ws) != NULL, "pane_focused should return a pane");
    lmux_app_free(app); teardown(); PASS();
}

/* Notifications */
static void test_notification_create(void) {
    TEST("notification_create adds notification to app queue");
    lmux_app *app = make_app();
    lmux_notify(app, "test notification", false);
    ASSERT(lmux_notification_count(app) == 1, "app should have 1 notification");
    const lmux_notification *n = lmux_notification_at(app, 0);
    ASSERT(n != NULL, "notification_at should return notification");
    ASSERT(strcmp(n->text, "test notification") == 0, "text should match");
    lmux_app_free(app); teardown(); PASS();
}

static void test_notification_clear(void) {
    TEST("notification_mark_read marks a notification as read");
    lmux_app *app = make_app();
    lmux_notify(app, "first", false);
    lmux_notify(app, "second", false);
    ASSERT(lmux_notification_count(app) == 2, "two notifications");
    const lmux_notification *n = lmux_notification_at(app, 0);
    lmux_notification_mark_read(app, n->seq);
    /* mark_read sets waiting=false but does NOT remove from list */
    ASSERT(lmux_notification_count(app) == 2, "mark_read does not remove notification");
    const lmux_notification *n2 = lmux_notification_at(app, 0);
    ASSERT(n2->waiting == false, "notification waiting should be false after mark_read");
    lmux_app_free(app); teardown(); PASS();
}

/* Agent management */
static void test_agent_spawn_fails_gracefully(void) {
    TEST("agent_spawn returns an error agent for missing command");
    lmux_app *app = make_app();
    lmux_id id = lmux_agent_spawn(app, "test-agent", "/nonexistent/command", 0);
    ASSERT(id > 0, "agent_spawn should return an id");
    lmux_agent *ag = lmux_agent_get(app, id);
    ASSERT(ag != NULL, "agent_get should find the agent");
    ASSERT(strcmp(ag->name, "test-agent") == 0, "name should match");
    lmux_app_free(app); teardown(); PASS();
}

static void test_agent_list(void) {
    TEST("agent_list returns agent count");
    lmux_app *app = make_app();
    size_t count = 0;
    lmux_agent **list = lmux_agent_list(app, &count);
    ASSERT(list == NULL || count == 0, "no agents should exist initially");
    if (list) free(list);
    lmux_app_free(app); teardown(); PASS();
}

/* Config */
static void test_config_defaults(void) {
    TEST("config_new sets sensible defaults");
    lmux_config *cfg = lmux_config_new();
    ASSERT(cfg != NULL, "config_new should succeed");
    ASSERT(strcmp(cfg->font_family, "monospace") == 0, "default font family");
    ASSERT(cfg->font_size == 12, "default font size");
    ASSERT(strcmp(cfg->theme, "dark") == 0, "default theme");
    ASSERT(cfg->keybindings.len == 0, "no default keybindings");
    ASSERT(cfg->workspace_groups.len == 0, "no default groups");
    lmux_config_free(cfg); PASS();
}

static void test_config_load_missing_file(void) {
    TEST("config_load returns false for missing file");
    lmux_config *cfg = lmux_config_new();
    bool ok = lmux_config_load(cfg, "/nonexistent/path/config.json");
    ASSERT(ok == false, "missing file should return false");
    lmux_config_free(cfg); PASS();
}

static void test_keybinding_find(void) {
    TEST("config_find_key returns NULL for unknown key");
    lmux_config *cfg = lmux_config_new();
    lmux_keybinding *kb = lmux_config_find_key(cfg, "nonexistent");
    ASSERT(kb == NULL, "unknown key should return NULL");
    lmux_config_free(cfg); PASS();
}

/* Dispatch / command tests */
static void test_dispatch_ping(void) {
    TEST("dispatch ping returns version");
    lmux_app *app = make_app();
    char *resp = lmux_dispatch_json(app, "{\"cmd\":\"ping\",\"args\":{}}");
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":true"), "ping should succeed");
    ASSERT(strstr(resp, LMUX_VERSION), "should contain version");
    free(resp);
    lmux_app_free(app); teardown(); PASS();
}

static void test_dispatch_health_live(void) {
    TEST("dispatch health.live returns alive status");
    lmux_app *app = make_app();
    char *resp = lmux_dispatch_json(app, "{\"cmd\":\"health.live\",\"args\":{}}");
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":true"), "health.live should succeed");
    ASSERT(strstr(resp, "\"status\":\"alive\""), "should report alive");
    ASSERT(strstr(resp, "\"pid\":"), "should contain pid");
    free(resp);
    lmux_app_free(app); teardown(); PASS();
}

static void test_dispatch_health_ready(void) {
    TEST("dispatch health.ready returns status");
    lmux_app *app = make_app();
    char *resp = lmux_dispatch_json(app, "{\"cmd\":\"health.ready\",\"args\":{}}");
    ASSERT(resp != NULL, "response should not be NULL");
    /* No server running → listen_fd == -1 → not_ready is expected */
    ASSERT(strstr(resp, "\"status\":\"not_ready\""), "should report not_ready without server");
    ASSERT(strstr(resp, "\"workspaces\":"), "should contain workspaces count");
    free(resp);
    lmux_app_free(app); teardown(); PASS();
}

static void test_dispatch_metrics(void) {
    TEST("dispatch metrics returns counters");
    lmux_app *app = make_app();
    /* Run a ping to bump counters */
    free(lmux_dispatch_json(app, "{\"cmd\":\"ping\",\"args\":{}}"));
    char *resp = lmux_dispatch_json(app, "{\"cmd\":\"metrics\",\"args\":{}}");
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":true"), "metrics should succeed");
    ASSERT(strstr(resp, "\"requests\":"), "should contain requests");
    ASSERT(strstr(resp, "\"connections\":"), "should contain connections");
    free(resp);
    lmux_app_free(app); teardown(); PASS();
}

static void test_dispatch_unknown_cmd(void) {
    TEST("dispatch unknown command returns error");
    lmux_app *app = make_app();
    char *resp = lmux_dispatch_json(app, "{\"cmd\":\"nope.zzz\",\"args\":{}}");
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":false"), "unknown cmd should fail");
    free(resp);
    lmux_app_free(app); teardown(); PASS();
}

static void test_snapshot_backup_recovery(void) {
    TEST("snapshot save creates .bak and load recovers from it");
    lmux_app *app = make_app();
    /* Create a workspace so we have something to save */
    lmux_workspace *ws = lmux_workspace_create(app, "backup-test");
    ASSERT(ws != NULL, "workspace created");
    size_t count_before = lmux_workspace_count(app);

    /* Save snapshot */
    char path[256];
    snprintf(path, sizeof path, "/tmp/lmux-snap-test-%d.json", getpid());
    ASSERT(lmux_snapshot_save(app, path), "snapshot_save should succeed");

    /* .bak should exist */
    char bak[260];
    snprintf(bak, sizeof bak, "%s.bak", path);
    struct stat st;
    ASSERT(stat(bak, &st) == 0, ".bak file should exist");

    /* Corrupt the main file */
    FILE *f = fopen(path, "w");
    fprintf(f, "NOT_JSON");
    fclose(f);

    /* Load into a fresh app — should recover from .bak */
    unlink("/tmp/lmux-snap-recovery.sock");
    lmux_app *app2 = lmux_app_new("/tmp/lmux-snap-recovery.sock");
    bool ok = lmux_snapshot_load_with_recovery(app2, path);
    ASSERT(ok, "load_with_recovery should succeed from .bak");
    size_t count_after = lmux_workspace_count(app2);
    ASSERT(count_after >= count_before, "should have restored workspaces");

    unlink(path);
    unlink(bak);
    lmux_app_free(app);
    lmux_app_free(app2);
    teardown(); PASS();
}

int main(void) {
    printf("lmux Model Unit Tests\n");
    printf("=====================\n\n");
    test_workspace_create();
    test_workspace_by_id();
    test_workspace_by_index();
    test_workspace_close();
    test_workspace_ssh_create();
    test_surface_create();
    test_surface_focused();
    test_pane_split();
    test_pane_focused();
    test_notification_create();
    test_notification_clear();
    test_agent_spawn_fails_gracefully();
    test_agent_list();
    test_config_defaults();
    test_config_load_missing_file();
    test_keybinding_find();
    test_dispatch_ping();
    test_dispatch_health_live();
    test_dispatch_health_ready();
    test_dispatch_metrics();
    test_dispatch_unknown_cmd();
    test_snapshot_backup_recovery();
    printf("\n=====================\n");
    printf("Tests: %d passed, %d failed, %d total\n\n",
           tests_pass, tests_fail, tests_run);
    return tests_fail > 0 ? 1 : 0;
}
