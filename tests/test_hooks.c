/**
 * test_hooks.c — Unit tests for lmux hook registry and dispatcher.
 *
 * Tests named hook registry, after-* dispatch, monitor hooks,
 * and control notifications.
 */
#define _POSIX_C_SOURCE 200809L
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

/* Test hook registration and listing */
static void test_hook_register_list(void) {
    TEST("hook register and list core hooks");
    lmux_app *app = make_app();
    
    /* Register hooks for the 8 core events */
    int rc = lmux_hook_add(app, "session-created", "echo session created");
    ASSERT(rc == 0, "session-created hook should register");
    
    rc = lmux_hook_add(app, "session-closed", "echo session closed");
    ASSERT(rc == 0, "session-closed hook should register");
    
    rc = lmux_hook_add(app, "session-renamed", "echo session renamed");
    ASSERT(rc == 0, "session-renamed hook should register");
    
    rc = lmux_hook_add(app, "window-created", "echo window created");
    ASSERT(rc == 0, "window-created hook should register");
    
    rc = lmux_hook_add(app, "window-closed", "echo window closed");
    ASSERT(rc == 0, "window-closed hook should register");
    
    rc = lmux_hook_add(app, "window-renamed", "echo window renamed");
    ASSERT(rc == 0, "window-renamed hook should register");
    
    rc = lmux_hook_add(app, "pane-created", "echo pane created");
    ASSERT(rc == 0, "pane-created hook should register");
    
    rc = lmux_hook_add(app, "pane-closed", "echo pane closed");
    ASSERT(rc == 0, "pane-closed hook should register");
    
    rc = lmux_hook_add(app, "pane-focused", "echo pane focused");
    ASSERT(rc == 0, "pane-focused hook should register");
    
    /* List hooks */
    size_t count = lmux_hook_count(app);
    ASSERT(count == 9, "should have 9 hooks registered");
    
    /* Check each hook exists */
    for (size_t i = 0; i < count; i++) {
        const lmux_hook *h = lmux_hook_at(app, i);
        ASSERT(h != NULL, "hook_at should return valid hook");
        ASSERT(h->name[0] != '\0', "hook should have name");
        ASSERT(h->command[0] != '\0', "hook should have command");
    }
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test hook removal */
static void test_hook_remove(void) {
    TEST("hook remove works");
    lmux_app *app = make_app();
    
    lmux_hook_add(app, "session-created", "echo test");
    lmux_hook_add(app, "session-closed", "echo test2");
    
    ASSERT(lmux_hook_count(app) == 2, "should have 2 hooks");
    
    bool ok = lmux_hook_remove(app, "session-created");
    ASSERT(ok, "remove should succeed");
    ASSERT(lmux_hook_count(app) == 1, "should have 1 hook after remove");
    
    ok = lmux_hook_remove(app, "nonexistent");
    ASSERT(!ok, "remove nonexistent should fail");
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test hook firing on workspace create/close/rename */
static void test_hook_fire_workspace(void) {
    TEST("hook fires on workspace create/close/rename");
    lmux_app *app = make_app();
    
    /* Register hooks */
    lmux_hook_add(app, "session-created", "echo created");
    lmux_hook_add(app, "session-closed", "echo closed");
    lmux_hook_add(app, "session-renamed", "echo renamed");
    
    /* Create workspace - should fire session-created */
    lmux_workspace *ws = lmux_workspace_create(app, "test-ws");
    ASSERT(ws != NULL, "workspace created");
    
    /* Check control notification was emitted (via event queue) */
    char *event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued");
    ASSERT(strstr(event, "session-created") != NULL, "event should mention session-created");
    free(event);
    
    /* Rename workspace - should fire session-renamed */
    lmux_workspace_set_title(ws, "renamed-ws");
    event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued for rename");
    ASSERT(strstr(event, "session-renamed") != NULL, "event should mention session-renamed");
    free(event);
    
    /* Close workspace - should fire session-closed */
    lmux_workspace_close(app, ws);
    event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued for close");
    ASSERT(strstr(event, "session-closed") != NULL, "event should mention session-closed");
    free(event);
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test hook firing on surface create/close/rename */
static void test_hook_fire_surface(void) {
    TEST("hook fires on surface/window create/close/rename");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    
    /* Register hooks */
    lmux_hook_add(app, "window-created", "echo window created");
    lmux_hook_add(app, "window-closed", "echo window closed");
    lmux_hook_add(app, "window-renamed", "echo window renamed");
    
    /* Create surface - should fire window-created */
    lmux_surface *surf = lmux_surface_create(ws, "my-surface");
    ASSERT(surf != NULL, "surface created");
    
    char *event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued");
    ASSERT(strstr(event, "window-created") != NULL, "event should mention window-created");
    free(event);
    
    /* Rename surface - should fire window-renamed */
    lmux_surface_set_title(surf, "renamed-surface");
    event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued for rename");
    ASSERT(strstr(event, "window-renamed") != NULL, "event should mention window-renamed");
    free(event);
    
    /* Close surface - should fire window-closed */
    lmux_surface_close(ws, surf);
    event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued for close");
    ASSERT(strstr(event, "window-closed") != NULL, "event should mention window-closed");
    free(event);
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test hook firing on pane create/close/focus */
static void test_hook_fire_pane(void) {
    TEST("hook fires on pane create/close/focus");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    
    /* Register hooks */
    lmux_hook_add(app, "pane-created", "echo pane created");
    lmux_hook_add(app, "pane-closed", "echo pane closed");
    lmux_hook_add(app, "pane-focused", "echo pane focused");
    
    /* Create pane - should fire pane-created */
    lmux_pane *pane = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "echo test", false);
    ASSERT(pane != NULL, "pane created");
    
    char *event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued");
    ASSERT(strstr(event, "pane-created") != NULL, "event should mention pane-created");
    free(event);
    
    /* Focus pane - should fire pane-focused */
    lmux_pane_focus(ws, pane);
    event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued for focus");
    ASSERT(strstr(event, "pane-focused") != NULL, "event should mention pane-focused");
    free(event);
    
    /* Close pane - should fire pane-closed */
    lmux_pane_close(ws, pane);
    event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued for close");
    ASSERT(strstr(event, "pane-closed") != NULL, "event should mention pane-closed");
    free(event);
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test after-* hooks */
static void test_after_hooks(void) {
    TEST("after-* hooks fire after operations");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    
    /* Register after-* hooks */
    lmux_hook_add(app, "after-session-created", "echo after session created");
    lmux_hook_add(app, "after-window-created", "echo after window created");
    lmux_hook_add(app, "after-pane-created", "echo after pane created");
    lmux_hook_add(app, "after-pane-closed", "echo after pane closed");
    lmux_hook_add(app, "after-attach", "echo after attach");
    lmux_hook_add(app, "after-detach", "echo after detach");
    lmux_hook_add(app, "after-split", "echo after split");
    lmux_hook_add(app, "after-resize", "echo after resize");
    
    /* Create workspace - should fire after-session-created, after-window-created, after-pane-created */
    lmux_workspace *ws2 = lmux_workspace_create(app, "ws2");
    ASSERT(ws2 != NULL, "workspace created");
    
    /* Pop 3 events from ws2 creation (after-session-created, after-window-created, after-pane-created) */
    for (int i = 0; i < 3; i++) {
        char *e = lmux_event_pop(app);
        ASSERT(e != NULL, "ws2 creation event should be queued");
        free(e);
    }
    
    /* Create surface - should fire after-window-created and after-pane-created (default pane) */
    lmux_surface *surf2 = lmux_surface_create(ws, "surf2");
    ASSERT(surf2 != NULL, "surface created");
    
    /* Pop 2 events from surf2 creation */
    for (int i = 0; i < 2; i++) {
        char *e = lmux_event_pop(app);
        ASSERT(e != NULL, "surf2 creation event should be queued");
        free(e);
    }
    
    /* Create pane - should fire after-pane-created and after-split */
    lmux_pane *pane = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "echo test", false);
    ASSERT(pane != NULL, "pane created");
    
    char *event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued");
    ASSERT(strstr(event, "after-pane-created") != NULL, "event should mention after-pane-created");
    free(event);
    
    event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued for after-split");
    ASSERT(strstr(event, "after-split") != NULL, "event should mention after-split");
    free(event);
    
    /* Close pane - should fire after-pane-closed */
    lmux_pane_close(ws, pane);
    event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued");
    ASSERT(strstr(event, "after-pane-closed") != NULL, "event should mention after-pane-closed");
    free(event);
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test monitor hooks */
static void test_monitor_hooks(void) {
    TEST("monitor hooks registration and configuration");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane *pane = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "echo test", false);
    
    /* Register monitor hooks */
    lmux_hook_add(app, "monitor-activity", "echo activity");
    lmux_hook_add(app, "monitor-bell", "echo bell");
    lmux_hook_add(app, "monitor-silence", "echo silence");
    
    size_t count = lmux_hook_count(app);
    ASSERT(count == 3, "should have 3 monitor hooks");
    
    /* Set monitor intervals */
    bool ok = lmux_pane_set_monitor_activity_interval(pane, 1000);
    ASSERT(ok, "set monitor-activity-interval should succeed");
    
    ok = lmux_pane_set_monitor_silence_interval(pane, 5000);
    ASSERT(ok, "set monitor-silence-interval should succeed");
    
    /* Set monitor actions */
    ok = lmux_pane_set_monitor_activity_action(pane, "any");
    ASSERT(ok, "set monitor-activity-action should succeed");
    
    ok = lmux_pane_set_monitor_bell_action(pane, "current");
    ASSERT(ok, "set monitor-bell-action should succeed");
    
    ok = lmux_pane_set_monitor_silence_action(pane, "other");
    ASSERT(ok, "set monitor-silence-action should succeed");
    
    /* Get monitor settings */
    int activity_interval = lmux_pane_get_monitor_activity_interval(pane);
    ASSERT(activity_interval == 1000, "activity interval should be 1000");
    
    int silence_interval = lmux_pane_get_monitor_silence_interval(pane);
    ASSERT(silence_interval == 5000, "silence interval should be 5000");
    
    const char *activity_action = lmux_pane_get_monitor_activity_action(pane);
    ASSERT(strcmp(activity_action, "any") == 0, "activity action should be 'any'");
    
    const char *bell_action = lmux_pane_get_monitor_bell_action(pane);
    ASSERT(strcmp(bell_action, "current") == 0, "bell action should be 'current'");
    
    const char *silence_action = lmux_pane_get_monitor_silence_action(pane);
    ASSERT(strcmp(silence_action, "other") == 0, "silence action should be 'other'");
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test hook scope */
static void test_hook_scope(void) {
    TEST("hook scope filtering works");
    lmux_app *app = make_app();
    lmux_workspace *ws1 = lmux_workspace_create(app, "ws1");
    lmux_workspace *ws2 = lmux_workspace_create(app, "ws2");
    lmux_surface *surf1 = lmux_surface_create(ws1, "surf1");
    lmux_surface *surf2 = lmux_surface_create(ws2, "surf2");
    
    /* Register session-scoped hook for ws1 only */
    lmux_hook_add_scoped(app, "session-renamed", LMUX_HOOK_SCOPE_SESSION, lmux_workspace_get_id(ws1), "echo ws1 renamed");
    
    /* Register window-scoped hook for surf1 only */
    lmux_hook_add_scoped(app, "window-renamed", LMUX_HOOK_SCOPE_WINDOW, lmux_surface_get_id(surf1), "echo surf1 renamed");
    
    /* Register pane-scoped hook */
    lmux_pane *pane1 = lmux_pane_split(ws1, surf1, LMUX_SPLIT_HORIZONTAL, "echo test", false);
    lmux_hook_add_scoped(app, "pane-focused", LMUX_HOOK_SCOPE_PANE, lmux_pane_get_id(pane1), "echo pane1 focused");
    
    /* Rename ws1 - should fire session-renamed hook */
    lmux_workspace_set_title(ws1, "ws1-renamed");
    char *event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued");
    ASSERT(strstr(event, "session-renamed") != NULL, "should fire session-renamed");
    free(event);
    
    /* Rename ws2 - should NOT fire session-renamed hook (no hook for ws2) */
    lmux_workspace_set_title(ws2, "ws2-renamed");
    event = lmux_event_pop(app);
    ASSERT(event == NULL, "no event should be queued for ws2 rename");
    
    /* Rename surf1 - should fire window-renamed hook */
    lmux_surface_set_title(surf1, "surf1-renamed");
    event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued");
    ASSERT(strstr(event, "window-renamed") != NULL, "should fire window-renamed");
    free(event);
    
    /* Rename surf2 - should NOT fire window-renamed hook */
    lmux_surface_set_title(surf2, "surf2-renamed");
    event = lmux_event_pop(app);
    ASSERT(event == NULL, "no event should be queued for surf2 rename");
    
    /* Focus pane1 - should fire pane-focused hook */
    lmux_pane_focus(ws1, pane1);
    event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued");
    ASSERT(strstr(event, "pane-focused") != NULL, "should fire pane-focused");
    free(event);
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test control notification for hook fired */
static void test_hook_control_notification(void) {
    TEST("control notification emitted when hook fires");
    lmux_app *app = make_app();
    
    /* Register a control client */
    int slot = lmux_control_register();
    ASSERT(slot >= 0, "control register should succeed");
    
    /* Subscribe to hook events */
    int rc = lmux_control_subscribe(slot, "hooks", "hook", "");
    ASSERT(rc == 0, "control subscribe should succeed");
    
    /* Register hook */
    lmux_hook_add(app, "session-created", "echo test");
    
    /* Create workspace - should fire hook and emit control notification */
    lmux_workspace *ws = lmux_workspace_create(app, "test-ws");
    ASSERT(ws != NULL, "workspace created");
    
    /* Pop control notification */
    char *lines[16];
    size_t n = lmux_control_pop_pending(slot, lines, 16);
    ASSERT(n > 0, "should have control notification");
    
    bool found = false;
    for (size_t i = 0; i < n; i++) {
        if (strstr(lines[i], "%hook-fired") != NULL) {
            found = true;
            ASSERT(strstr(lines[i], "session-created") != NULL, "notification should mention hook name");
            ASSERT(strstr(lines[i], "session") != NULL, "notification should mention scope");
        }
        free(lines[i]);
    }
    ASSERT(found, "should have %hook-fired notification");
    
    lmux_control_unregister(slot);
    lmux_app_free(app); teardown(); PASS();
}

/* Test hook enabled/disabled flag */
static void test_hook_enabled_flag(void) {
    TEST("hook enabled flag controls firing");
    lmux_app *app = make_app();
    
    /* Register hook */
    lmux_hook_add(app, "session-created", "echo test");
    
    /* Disable hook */
    bool ok = lmux_hook_set_enabled(app, "session-created", false);
    ASSERT(ok, "set enabled should succeed");
    
    /* Create workspace - hook should NOT fire */
    lmux_workspace *ws = lmux_workspace_create(app, "test-ws");
    ASSERT(ws != NULL, "workspace created");
    
    char *event = lmux_event_pop(app);
    ASSERT(event == NULL, "no event should be queued when hook disabled");
    
    /* Enable hook */
    ok = lmux_hook_set_enabled(app, "session-created", true);
    ASSERT(ok, "set enabled should succeed");
    
    /* Create another workspace - hook SHOULD fire */
    lmux_workspace *ws2 = lmux_workspace_create(app, "test-ws2");
    ASSERT(ws2 != NULL, "workspace created");
    
    event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued when hook enabled");
    ASSERT(strstr(event, "session-created") != NULL, "event should mention session-created");
    free(event);
    
    lmux_app_free(app); teardown(); PASS();
}

int main(void) {
    printf("lmux Hook Tests\n");
    printf("===============\n\n");
    
    test_hook_register_list();
    test_hook_remove();
    test_hook_fire_workspace();
    test_hook_fire_surface();
    test_hook_fire_pane();
    test_after_hooks();
    test_monitor_hooks();
    test_hook_scope();
    test_hook_control_notification();
    test_hook_enabled_flag();
    
    printf("\n===============\n");
    printf("Tests: %d passed, %d failed, %d total\n\n",
           tests_pass, tests_fail, tests_run);
    return tests_fail > 0 ? 1 : 0;
}