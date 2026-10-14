/**
 * test_session_groups.c — Unit tests for session groups + linked windows (ISS-0035).
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
    snprintf(sock_path, sizeof sock_path, "/tmp/lmux-test-sg-%d.sock", getpid());
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

/* Helper to build JSON with workspace IDs */
static char *build_link_cmd(lmux_id surface_id, lmux_id target_ws_id) {
    char *cmd = malloc(512);
    snprintf(cmd, 512, "{\"cmd\":\"surface.link\",\"args\":{\"surface_id\":%u,\"target_workspace_id\":%u}}", surface_id, target_ws_id);
    return cmd;
}

static char *build_unlink_cmd(lmux_id surface_id, lmux_id workspace_id) {
    char *cmd = malloc(512);
    snprintf(cmd, 512, "{\"cmd\":\"surface.unlink\",\"args\":{\"surface_id\":%u,\"workspace_id\":%u}}", surface_id, workspace_id);
    return cmd;
}

static char *build_list_surfaces_cmd(lmux_id ws_id) {
    char *cmd = malloc(512);
    snprintf(cmd, 512, "{\"cmd\":\"surface.list\",\"args\":{\"workspace_id\":%u}}", ws_id);
    return cmd;
}

static char *build_create_ws_cmd(const char *title, const char *group) {
    char *cmd = malloc(1024);
    if (group && group[0]) {
        snprintf(cmd, 1024, "{\"cmd\":\"workspace.create\",\"args\":{\"title\":\"%s\",\"group\":\"%s\"}}", title, group);
    } else {
        snprintf(cmd, 1024, "{\"cmd\":\"workspace.create\",\"args\":{\"title\":\"%s\"}}", title);
    }
    return cmd;
}

/* Session group creation via new-session -t (group) */
static void test_session_group_create(void) {
    TEST("session_group_create creates a group with two workspaces");
    lmux_app *app = make_app();
    
    /* Create first workspace in a group */
    char *cmd = build_create_ws_cmd("ws1", "mygroup");
    char *resp = dispatch(app, cmd);
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":true"), "workspace.create should succeed");
    free(resp);
    free(cmd);
    
    /* Create second workspace in the same group */
    cmd = build_create_ws_cmd("ws2", "mygroup");
    resp = dispatch(app, cmd);
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":true"), "workspace.create should succeed");
    free(resp);
    free(cmd);
    
    /* Verify both workspaces exist */
    resp = dispatch(app, "{\"cmd\":\"workspace.list\",\"args\":{}}");
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"title\":\"ws1\""), "ws1 should be listed");
    ASSERT(strstr(resp, "\"title\":\"ws2\""), "ws2 should be listed");
    free(resp);
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_session_group_list(void) {
    TEST("session_group_list shows groups and their workspaces");
    lmux_app *app = make_app();
    
    /* Create workspaces in different groups */
    dispatch(app, build_create_ws_cmd("ws1", "group1"));
    dispatch(app, build_create_ws_cmd("ws2", "group1"));
    dispatch(app, build_create_ws_cmd("ws3", "group2"));
    dispatch(app, build_create_ws_cmd("ws4", NULL)); /* no group */
    
    char *resp = dispatch(app, "{\"cmd\":\"session.group.list\",\"args\":{}}");
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":true"), "session.group.list should succeed");
    ASSERT(strstr(resp, "\"group1\""), "group1 should be listed");
    ASSERT(strstr(resp, "\"group2\""), "group2 should be listed");
    free(resp);
    
    lmux_app_free(app); teardown(); PASS();
}

/* Link window (surface) across workspaces in same group */
static void test_link_window(void) {
    TEST("link_window links a surface from one workspace to another in same group");
    lmux_app *app = make_app();
    
    /* Create two workspaces in the same group */
    dispatch(app, build_create_ws_cmd("ws1", "mygroup"));
    dispatch(app, build_create_ws_cmd("ws2", "mygroup"));
    
    /* Get workspace IDs using public API */
    lmux_workspace *ws1 = lmux_workspace_by_index(app, 0);
    lmux_workspace *ws2 = lmux_workspace_by_index(app, 1);
    ASSERT(ws1 != NULL, "ws1 should exist");
    ASSERT(ws2 != NULL, "ws2 should exist");
    ASSERT(ws1 != ws2, "workspaces should be different");
    
    lmux_id ws1_id = lmux_workspace_get_id(ws1);
    lmux_id ws2_id = lmux_workspace_get_id(ws2);
    
    /* Create a surface in ws1 */
    lmux_surface *surf = lmux_surface_create(ws1, "shared-surface");
    ASSERT(surf != NULL, "surface should be created");
    lmux_id surf_id = lmux_surface_get_id(surf);
    
    /* Link the surface to ws2 */
    char *cmd = build_link_cmd(surf_id, ws2_id);
    char *resp = dispatch(app, cmd);
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":true"), "surface.link should succeed");
    free(resp);
    free(cmd);
    
    /* Verify surface appears in both workspaces */
    ASSERT(lmux_surface_count(ws1) >= 1, "ws1 should have the surface");
    ASSERT(lmux_surface_count(ws2) >= 1, "ws2 should have the linked surface");
    
    /* The surface should be the same object (shared pointer) - use public API */
    lmux_surface *found = lmux_surface_find_by_id(ws2, surf_id);
    ASSERT(found != NULL, "surface should be found in ws2");
    ASSERT(found == surf, "surface should be same object (shared)");
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_unlink_window(void) {
    TEST("unlink_window removes surface from one workspace but keeps it in others");
    lmux_app *app = make_app();
    
    /* Create two workspaces in the same group */
    dispatch(app, build_create_ws_cmd("ws1", "mygroup"));
    dispatch(app, build_create_ws_cmd("ws2", "mygroup"));
    
    lmux_workspace *ws1 = lmux_workspace_by_index(app, 0);
    lmux_workspace *ws2 = lmux_workspace_by_index(app, 1);
    ASSERT(ws1 != NULL && ws2 != NULL, "both workspaces should exist");
    
    lmux_id ws1_id = lmux_workspace_get_id(ws1);
    lmux_id ws2_id = lmux_workspace_get_id(ws2);
    
    /* Create a surface in ws1 and link to ws2 */
    lmux_surface *surf = lmux_surface_create(ws1, "shared-surface");
    lmux_id surf_id = lmux_surface_get_id(surf);
    dispatch(app, build_link_cmd(surf_id, ws2_id));
    
    /* Unlink from ws1 */
    char *cmd = build_unlink_cmd(surf_id, ws1_id);
    char *resp = dispatch(app, cmd);
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":true"), "surface.unlink should succeed");
    free(resp);
    free(cmd);
    
    /* Surface should be gone from ws1 but still in ws2 - use public API */
    lmux_surface *surf_in_ws1 = lmux_surface_find_by_id(ws1, surf_id);
    lmux_surface *surf_in_ws2 = lmux_surface_find_by_id(ws2, surf_id);
    ASSERT(surf_in_ws1 == NULL, "surface should be removed from ws1");
    ASSERT(surf_in_ws2 != NULL, "surface should still exist in ws2");
    ASSERT(surf_in_ws2 == surf, "surface should be same object");
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_destroy_source_workspace_keeps_surface(void) {
    TEST("destroying source workspace keeps surface alive in linked workspace");
    lmux_app *app = make_app();
    
    /* Create two workspaces in the same group */
    dispatch(app, build_create_ws_cmd("ws1", "mygroup"));
    dispatch(app, build_create_ws_cmd("ws2", "mygroup"));
    
    lmux_workspace *ws1 = lmux_workspace_by_index(app, 0);
    lmux_workspace *ws2 = lmux_workspace_by_index(app, 1);
    ASSERT(ws1 != NULL && ws2 != NULL, "both workspaces should exist");
    
    lmux_id ws1_id = lmux_workspace_get_id(ws1);
    lmux_id ws2_id = lmux_workspace_get_id(ws2);
    
    /* Create a surface in ws1 and link to ws2 */
    lmux_surface *surf = lmux_surface_create(ws1, "shared-surface");
    lmux_id surf_id = lmux_surface_get_id(surf);
    dispatch(app, build_link_cmd(surf_id, ws2_id));
    
    /* Close ws1 */
    lmux_workspace_close(app, ws1);
    
    /* ws2 should still have the surface - use public API */
    lmux_surface *surf_in_ws2 = lmux_surface_find_by_id(ws2, surf_id);
    ASSERT(surf_in_ws2 != NULL, "surface should still exist in ws2 after ws1 destroyed");
    ASSERT(surf_in_ws2 == surf, "surface should be same object");
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_link_window_cross_group_fails(void) {
    TEST("link_window fails when workspaces are in different groups");
    lmux_app *app = make_app();
    
    /* Create workspaces in different groups */
    dispatch(app, build_create_ws_cmd("ws1", "group1"));
    dispatch(app, build_create_ws_cmd("ws2", "group2"));
    
    lmux_workspace *ws1 = lmux_workspace_by_index(app, 0);
    lmux_workspace *ws2 = lmux_workspace_by_index(app, 1);
    ASSERT(ws1 != NULL && ws2 != NULL, "both workspaces should exist");
    
    lmux_id ws2_id = lmux_workspace_get_id(ws2);
    
    /* Create a surface in ws1 */
    lmux_surface *surf = lmux_surface_create(ws1, "shared-surface");
    lmux_id surf_id = lmux_surface_get_id(surf);
    
    /* Try to link to ws2 (different group) - should fail */
    char *cmd = build_link_cmd(surf_id, ws2_id);
    char *resp = dispatch(app, cmd);
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":false"), "surface.link should fail for different groups");
    free(resp);
    free(cmd);
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_link_window_ungrouped_fails(void) {
    TEST("link_window fails when target workspace is ungrouped");
    lmux_app *app = make_app();
    
    /* Create one workspace in a group, one ungrouped */
    dispatch(app, build_create_ws_cmd("ws1", "mygroup"));
    dispatch(app, build_create_ws_cmd("ws2", NULL)); /* no group */
    
    lmux_workspace *ws1 = lmux_workspace_by_index(app, 0);
    lmux_workspace *ws2 = lmux_workspace_by_index(app, 1);
    ASSERT(ws1 != NULL && ws2 != NULL, "both workspaces should exist");
    
    lmux_id ws2_id = lmux_workspace_get_id(ws2);
    
    /* Create a surface in ws1 */
    lmux_surface *surf = lmux_surface_create(ws1, "shared-surface");
    lmux_id surf_id = lmux_surface_get_id(surf);
    
    /* Try to link to ws2 (ungrouped) - should fail */
    char *cmd = build_link_cmd(surf_id, ws2_id);
    char *resp = dispatch(app, cmd);
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":false"), "surface.link should fail for ungrouped target");
    free(resp);
    free(cmd);
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_list_windows_shows_linked_count(void) {
    TEST("surface.list shows linked count for shared surfaces");
    lmux_app *app = make_app();
    
    /* Create two workspaces in the same group */
    dispatch(app, build_create_ws_cmd("ws1", "mygroup"));
    dispatch(app, build_create_ws_cmd("ws2", "mygroup"));
    
    lmux_workspace *ws1 = lmux_workspace_by_index(app, 0);
    lmux_workspace *ws2 = lmux_workspace_by_index(app, 1);
    ASSERT(ws1 != NULL && ws2 != NULL, "both workspaces should exist");
    
    lmux_id ws1_id = lmux_workspace_get_id(ws1);
    lmux_id ws2_id = lmux_workspace_get_id(ws2);
    
    /* Create a surface in ws1 and link to ws2 */
    lmux_surface *surf = lmux_surface_create(ws1, "shared-surface");
    lmux_id surf_id = lmux_surface_get_id(surf);
    dispatch(app, build_link_cmd(surf_id, ws2_id));
    
    /* List surfaces in ws1 - should show linked count */
    char *cmd = build_list_surfaces_cmd(ws1_id);
    char *resp = dispatch(app, cmd);
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":true"), "surface.list should succeed");
    ASSERT(strstr(resp, "\"linked_count\":2"), "surface in ws1 should show linked_count=2");
    free(resp);
    free(cmd);
    
    /* List surfaces in ws2 - should also show linked count */
    cmd = build_list_surfaces_cmd(ws2_id);
    resp = dispatch(app, cmd);
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":true"), "surface.list should succeed");
    ASSERT(strstr(resp, "\"linked_count\":2"), "surface in ws2 should show linked_count=2");
    free(resp);
    free(cmd);
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_control_notifications(void) {
    TEST("control notifications fire for session group changes and window link/unlink");
    lmux_app *app = make_app();
    
    /* Register a control client */
    int slot = lmux_control_register();
    ASSERT(slot >= 0, "control register should succeed");
    
    /* Subscribe to session and window events - use different names */
    lmux_control_subscribe(slot, "test_session", "session", "");
    lmux_control_subscribe(slot, "test_window", "window", "");
    
    /* Create workspace with group - should fire %session-group-changed */
    char *resp = dispatch(app, build_create_ws_cmd("ws1", "mygroup"));
    ASSERT(strstr(resp, "\"ok\":true"), "workspace.create should succeed");
    free(resp);
    
    /* Check for notification */
    char *lines[16];
    size_t n = lmux_control_pop_pending(slot, lines, 16);
    bool found_group_created = false;
    for (size_t i = 0; i < n; i++) {
        if (strstr(lines[i], "%session-group-changed") && strstr(lines[i], "created")) {
            found_group_created = true;
        }
        free(lines[i]);
    }
    ASSERT(found_group_created, "should receive %session-group-changed created notification");
    
    /* Create second workspace in group */
    resp = dispatch(app, build_create_ws_cmd("ws2", "mygroup"));
    ASSERT(strstr(resp, "\"ok\":true"), "workspace.create should succeed");
    free(resp);
    
    lmux_workspace *ws1 = lmux_workspace_by_index(app, 0);
    lmux_workspace *ws2 = lmux_workspace_by_index(app, 1);
    lmux_surface *surf = lmux_surface_create(ws1, "shared-surface");
    lmux_id surf_id = lmux_surface_get_id(surf);
    lmux_id ws2_id = lmux_workspace_get_id(ws2);
    
    /* Link window - should fire %window-linked */
    resp = dispatch(app, build_link_cmd(surf_id, ws2_id));
    ASSERT(strstr(resp, "\"ok\":true"), "surface.link should succeed");
    free(resp);
    
    n = lmux_control_pop_pending(slot, lines, 16);
    bool found_window_linked = false;
    for (size_t i = 0; i < n; i++) {
        if (strstr(lines[i], "%window-linked")) {
            found_window_linked = true;
        }
        free(lines[i]);
    }
    ASSERT(found_window_linked, "should receive %window-linked notification");
    
    /* Unlink window - should fire %window-unlinked */
    lmux_id ws1_id = lmux_workspace_get_id(ws1);
    resp = dispatch(app, build_unlink_cmd(surf_id, ws1_id));
    ASSERT(strstr(resp, "\"ok\":true"), "surface.unlink should succeed");
    free(resp);
    
    n = lmux_control_pop_pending(slot, lines, 16);
    bool found_window_unlinked = false;
    for (size_t i = 0; i < n; i++) {
        if (strstr(lines[i], "%window-unlinked")) {
            found_window_unlinked = true;
        }
        free(lines[i]);
    }
    ASSERT(found_window_unlinked, "should receive %window-unlinked notification");
    
    /* Close last workspace in group - should fire %session-group-changed destroyed */
    lmux_workspace_close(app, ws1);
    lmux_workspace_close(app, ws2);
    
    n = lmux_control_pop_pending(slot, lines, 16);
    bool found_group_destroyed = false;
    for (size_t i = 0; i < n; i++) {
        if (strstr(lines[i], "%session-group-changed") && strstr(lines[i], "destroyed")) {
            found_group_destroyed = true;
        }
        free(lines[i]);
    }
    ASSERT(found_group_destroyed, "should receive %session-group-changed destroyed notification");
    
    lmux_control_unregister(slot);
    lmux_app_free(app); teardown(); PASS();
}

int main(void) {
    printf("lmux Session Groups Unit Tests\n");
    printf("==============================\n\n");
    
    test_session_group_create();
    test_session_group_list();
    test_link_window();
    test_unlink_window();
    test_destroy_source_workspace_keeps_surface();
    test_link_window_cross_group_fails();
    test_link_window_ungrouped_fails();
    test_list_windows_shows_linked_count();
    test_control_notifications();
    
    printf("\n==============================\n");
    printf("Tests: %d passed, %d failed, %d total\n\n",
           tests_pass, tests_fail, tests_run);
    return tests_fail > 0 ? 1 : 0;
}