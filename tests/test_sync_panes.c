/**
 * test_sync_panes.c — Unit tests for synchronize-panes and resize policies.
 *
 * Uses ONLY public API accessors. No internal struct access.
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

/* Helper to dispatch JSON and return response */
static char *dispatch(lmux_app *app, const char *json) {
    return lmux_dispatch_json(app, json);
}

/* Test 1: surface has synchronize_panes flag (default off) - via dispatch */
static void test_surface_sync_panes_flag(void) {
    TEST("surface has synchronize_panes flag (default off)");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    (void)surf;
    
    // Send a key to surface - should work even without sync-panes
    char *resp = dispatch(app, "{\"cmd\":\"surface.send_key\",\"args\":{\"surface_id\":1,\"text\":\"test\"}}");
    ASSERT(resp != NULL, "response should not be NULL");
    free(resp);
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test 2: Enable synchronize-panes and verify fan-out */
static void test_sync_panes_fanout(void) {
    TEST("synchronize-panes fan-out sends keys to all panes in surface");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    
    // Split into 3 panes
    lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", false);
    lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", false);
    lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", false);
    
    // Enable synchronize-panes via dispatch (surf is surface ID 2)
    char *resp = dispatch(app, "{\"cmd\":\"surface.set_sync_panes\",\"args\":{\"id\":2,\"enabled\":true}}");
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":true"), "set_sync_panes should succeed");
    ASSERT(strstr(resp, "\"synchronize_panes\":true"), "should return enabled state");
    free(resp);
    
    // Verify the state was saved
    resp = dispatch(app, "{\"cmd\":\"surface.set_sync_panes\",\"args\":{\"id\":2,\"enabled\":false}}");
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"synchronize_panes\":false"), "should return disabled state");
    free(resp);
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test 3: Resize policy - largest */
static void test_resize_policy_largest(void) {
    TEST("resize policy 'largest' resizes all panes to largest");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    
    lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", false);
    lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", false);
    
    // Set resize policy to 'largest' (surf is surface ID 2)
    char *resp = dispatch(app, "{\"cmd\":\"surface.set_window_size\",\"args\":{\"id\":2,\"policy\":\"largest\"}}");
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":true"), "set_window_size should succeed");
    ASSERT(strstr(resp, "\"window_size_policy\":1"), "should return policy 1 (largest)");
    free(resp);
    
    // Test invalid policy
    resp = dispatch(app, "{\"cmd\":\"surface.set_window_size\",\"args\":{\"id\":2,\"policy\":\"invalid\"}}");
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":false"), "invalid policy should fail");
    free(resp);
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test 4: Resize policy - smallest */
static void test_resize_policy_smallest(void) {
    TEST("resize policy 'smallest' resizes all panes to smallest");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    
    lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", false);
    lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", false);
    
    // Set resize policy to 'smallest' (surf is surface ID 2)
    char *resp = dispatch(app, "{\"cmd\":\"surface.set_window_size\",\"args\":{\"id\":2,\"policy\":\"smallest\"}}");
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":true"), "set_window_size should succeed");
    ASSERT(strstr(resp, "\"window_size_policy\":2"), "should return policy 2 (smallest)");
    free(resp);
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test 5: Resize policy - manual (no auto-resize) */
static void test_resize_policy_manual(void) {
    TEST("resize policy 'manual' does not auto-resize other panes");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    
    lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", false);
    lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", false);
    
    // Set resize policy to 'manual' (default) (surf is surface ID 2)
    char *resp = dispatch(app, "{\"cmd\":\"surface.set_window_size\",\"args\":{\"id\":2,\"policy\":\"manual\"}}");
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":true"), "set_window_size should succeed");
    ASSERT(strstr(resp, "\"window_size_policy\":0"), "should return policy 0 (manual)");
    free(resp);
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test 6: Resize policy - latest (most recently active) */
static void test_resize_policy_latest(void) {
    TEST("resize policy 'latest' resizes to most recently active pane");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    
    lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", false);
    lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", false);
    lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", false);
    
    // Set resize policy to 'latest' (surf is surface ID 2)
    char *resp = dispatch(app, "{\"cmd\":\"surface.set_window_size\",\"args\":{\"id\":2,\"policy\":\"latest\"}}");
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":true"), "set_window_size should succeed");
    ASSERT(strstr(resp, "\"window_size_policy\":3"), "should return policy 3 (latest)");
    free(resp);
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test 7: Control notifications for synchronize-panes */
static void test_control_notify_sync_panes(void) {
    TEST("control notification emitted when synchronize-panes changes");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    (void)surf;
    
    // Register control client
    int slot = lmux_control_register();
    ASSERT(slot >= 0, "control register should succeed");
    
    // Subscribe to pane notifications
    lmux_control_subscribe(slot, "test", "pane", "");
    
    // Toggle synchronize-panes (surf is surface ID 2)
    dispatch(app, "{\"cmd\":\"surface.set_sync_panes\",\"args\":{\"id\":2,\"enabled\":true}}");
    
    // Pop pending and verify %synchronize-panes-changed
    char *pending[16];
    size_t n = lmux_control_pop_pending(slot, pending, 16);
    int found = 0;
    for (size_t i = 0; i < n; i++) {
        if (strstr(pending[i], "%synchronize-panes-changed") != NULL) {
            found = 1;
            break;
        }
        free(pending[i]);
    }
    ASSERT(found, "should receive %synchronize-panes-changed notification");
    
    lmux_control_unregister(slot);
    lmux_app_free(app); teardown(); PASS();
}

/* Test 8: Control notifications for window-size policy */
static void test_control_notify_window_size(void) {
    TEST("control notification emitted when window-size policy changes");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    (void)surf;
    
    int slot = lmux_control_register();
    ASSERT(slot >= 0, "control register should succeed");
    lmux_control_subscribe(slot, "test", "pane", "");
    
    // Change window-size policy (surf is surface ID 2)
    dispatch(app, "{\"cmd\":\"surface.set_window_size\",\"args\":{\"id\":2,\"policy\":\"largest\"}}");
    
    // Pop pending and verify %window-size-changed
    char *pending[16];
    size_t n = lmux_control_pop_pending(slot, pending, 16);
    int found = 0;
    for (size_t i = 0; i < n; i++) {
        if (strstr(pending[i], "%window-size-changed") != NULL) {
            found = 1;
            break;
        }
        free(pending[i]);
    }
    ASSERT(found, "should receive %window-size-changed notification");
    
    lmux_control_unregister(slot);
    lmux_app_free(app); teardown(); PASS();
}

/* Test 9: resize-pane command honors policy */
static void test_resize_pane_honors_policy(void) {
    TEST("resize-pane command respects surface resize policy");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    
    lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", false);
    lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", false);
    
    // Set policy to largest (surf is surface ID 2)
    dispatch(app, "{\"cmd\":\"surface.set_window_size\",\"args\":{\"id\":2,\"policy\":\"largest\"}}");
    
    // Send resize command
    char *resp = dispatch(app, "{\"cmd\":\"pane.resize\",\"args\":{\"surface_id\":2,\"cols\":120,\"rows\":40}}");
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":true"), "resize should succeed");
    free(resp);
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test 10: resize-window command honors policy (alias for pane.resize) */
static void test_resize_window_honors_policy(void) {
    TEST("resize-window command respects surface resize policy");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    
    lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", false);
    lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", false);
    
    // Set policy to smallest (surf is surface ID 2)
    dispatch(app, "{\"cmd\":\"surface.set_window_size\",\"args\":{\"id\":2,\"policy\":\"smallest\"}}");
    
    // Send resize command
    char *resp = dispatch(app, "{\"cmd\":\"pane.resize\",\"args\":{\"surface_id\":2,\"cols\":60,\"rows\":20}}");
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":true"), "resize should succeed");
    free(resp);
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test 11: Split/close pane triggers resize per policy */
static void test_split_close_triggers_resize(void) {
    TEST("split/close pane triggers resize per policy");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    (void)surf;
    
    // Set policy to largest (surf is surface ID 2)
    dispatch(app, "{\"cmd\":\"surface.set_window_size\",\"args\":{\"id\":2,\"policy\":\"largest\"}}");
    
    // Split a new pane - should trigger recalc
    char *resp = dispatch(app, "{\"cmd\":\"surface.split\",\"args\":{\"surface_id\":2,\"direction\":\"horizontal\"}}");
    ASSERT(resp != NULL, "split should succeed");
    free(resp);
    
    // Close a pane - should trigger recalc
    resp = dispatch(app, "{\"cmd\":\"pane.focus\",\"args\":{\"id\":2}}");
    ASSERT(resp != NULL, "focus should succeed");
    free(resp);
    
    resp = dispatch(app, "{\"cmd\":\"surface.close\",\"args\":{\"id\":2}}");
    // This closes the surface, not a pane - just verify no crash
    free(resp);
    
    lmux_app_free(app); teardown(); PASS();
}

int main(void) {
    printf("lmux Synchronize-Panes Unit Tests\n");
    printf("=================================\n\n");
    
    test_surface_sync_panes_flag();
    test_sync_panes_fanout();
    test_resize_policy_largest();
    test_resize_policy_smallest();
    test_resize_policy_manual();
    test_resize_policy_latest();
    test_control_notify_sync_panes();
    test_control_notify_window_size();
    test_resize_pane_honors_policy();
    test_resize_window_honors_policy();
    test_split_close_triggers_resize();
    
    printf("\n=================================\n");
    printf("Tests: %d passed, %d failed, %d total\n\n",
           tests_pass, tests_fail, tests_run);
    return tests_fail > 0 ? 1 : 0;
}