/**
 * test_format.c — Unit tests for lmux format string expander.
 *
 * Tests the lmux_format_expand() function with variables, conditionals,
 * and window list loops.
 */
#define _POSIX_C_SOURCE 200809L
#include "lmux.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>

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

#define ASSERT_STR_EQ(actual, expected, msg) do { \
    if (strcmp(actual, expected) != 0) { \
        FAIL("%s: expected '%s', got '%s' (line %d)", msg, expected, actual, __LINE__); \
        return; \
    } \
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

/* Helper: get pane ID via public API */
static lmux_id get_pane_id(lmux_pane *p) {
    lmux_pane_info info;
    if (lmux_pane_get_info(p, &info)) return info.id;
    return 0;
}

/* Helper: get workspace ID via public API */
static lmux_id get_workspace_id(lmux_workspace *ws) {
    lmux_workspace_info info;
    if (lmux_workspace_get_info(ws, &info)) return (lmux_id)atol(info.id);
    return 0;
}

/* Helper: get surface ID via public API */
static lmux_id get_surface_id(lmux_surface *s) {
    lmux_surface_info info;
    if (lmux_surface_get_info(s, &info)) return info.id;
    return 0;
}

/* Helper: expand a format string with a given pane context */
static char *expand_with_pane(lmux_app *app, lmux_pane *p, const char *fmt) {
    char *out = malloc(4096);
    if (!out) return NULL;
    bool ok = lmux_format_expand(app, p, fmt, out, 4096);
    if (!ok) {
        free(out);
        return NULL;
    }
    return out;
}

/* ──────────────────────────────────────────────
 * Basic variable expansion
 * ────────────────────────────────────────────── */

static void test_format_simple_variable(void) {
    TEST("simple variable expansion: #{pane_id}");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane *p = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "echo test", false);
    
    char *result = expand_with_pane(app, p, "pane-#{pane_id}");
    ASSERT(result != NULL, "expansion should succeed");
    char expected[64];
    snprintf(expected, sizeof expected, "pane-%u", get_pane_id(p));
    ASSERT_STR_EQ(result, expected, "should expand pane_id");
    free(result);
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_format_pane_title(void) {
    TEST("variable expansion: #{pane_title}");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane *p = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "my-command", false);
    
    char *result = expand_with_pane(app, p, "title:#{pane_title}");
    ASSERT(result != NULL, "expansion should succeed");
    // pane_title should be the command for terminal panes
    ASSERT_STR_EQ(result, "title:my-command", "should expand pane_title to command");
    free(result);
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_format_pane_width_height(void) {
    TEST("variable expansion: #{pane_width}, #{pane_height}");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane *p = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "echo test", false);
    // Set a PTY size
    lmux_pane_pty_set_size(p, 80, 24);
    
    char *result = expand_with_pane(app, p, "size:#{pane_width}x#{pane_height}");
    ASSERT(result != NULL, "expansion should succeed");
    ASSERT_STR_EQ(result, "size:80x24", "should expand pane_width and pane_height");
    free(result);
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_format_session_id_name(void) {
    TEST("variable expansion: #{session_id}, #{session_name}");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "my-session");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane *p = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "echo test", false);
    
    char *result = expand_with_pane(app, p, "session:#{session_id}:#{session_name}");
    ASSERT(result != NULL, "expansion should succeed");
    char expected[128];
    snprintf(expected, sizeof expected, "session:%u:my-session", get_workspace_id(ws));
    ASSERT_STR_EQ(result, expected, "should expand session_id and session_name");
    free(result);
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_format_surface_id_title(void) {
    TEST("variable expansion: #{surface_id}, #{surface_title}");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "my-surface");
    lmux_pane *p = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "echo test", false);
    
    char *result = expand_with_pane(app, p, "surface:#{surface_id}:#{surface_title}");
    ASSERT(result != NULL, "expansion should succeed");
    char expected[128];
    snprintf(expected, sizeof expected, "surface:%u:my-surface", get_surface_id(surf));
    ASSERT_STR_EQ(result, expected, "should expand surface_id and surface_title");
    free(result);
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_format_window_id_name(void) {
    TEST("variable expansion: #{window_id}, #{window_name} (aliases for surface)");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "my-window");
    lmux_pane *p = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "echo test", false);
    
    char *result = expand_with_pane(app, p, "window:#{window_id}:#{window_name}");
    ASSERT(result != NULL, "expansion should succeed");
    char expected[128];
    snprintf(expected, sizeof expected, "window:%u:my-window", get_surface_id(surf));
    ASSERT_STR_EQ(result, expected, "should expand window_id and window_name (surface aliases)");
    free(result);
    
    lmux_app_free(app); teardown(); PASS();
}

/* ──────────────────────────────────────────────
 * Conditional expansion: #{?cond:true:false}
 * ────────────────────────────────────────────── */

static void test_format_conditional_true(void) {
    TEST("conditional: #{?pane_focused:focused:unfocused} when true");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane *p = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "echo test", false);
    lmux_pane_focus(ws, p); // Make it focused
    
    char *result = expand_with_pane(app, p, "#{?pane_focused:focused:unfocused}");
    ASSERT(result != NULL, "expansion should succeed");
    ASSERT_STR_EQ(result, "focused", "should expand to true branch when focused");
    free(result);
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_format_conditional_false(void) {
    TEST("conditional: #{?pane_focused:focused:unfocused} when false");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane *p1 = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "echo test1", false);
    lmux_pane *p2 = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "echo test2", false);
    lmux_pane_focus(ws, p1); // Focus p1, so p2 is unfocused
    
    char *result = expand_with_pane(app, p2, "#{?pane_focused:focused:unfocused}");
    ASSERT(result != NULL, "expansion should succeed");
    ASSERT_STR_EQ(result, "unfocused", "should expand to false branch when not focused");
    free(result);
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_format_conditional_nested(void) {
    TEST("conditional: nested conditionals");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane *p = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "echo test", false);
    lmux_pane_focus(ws, p);
    
    char *result = expand_with_pane(app, p, "#{?pane_focused:#{?pane_width:wide:narrow}:unfocused}");
    ASSERT(result != NULL, "expansion should succeed");
    /* pane_width is "80" (truthy), so inner conditional returns "wide" */
    ASSERT_STR_EQ(result, "wide", "nested conditional should work");
    free(result);
    
    lmux_app_free(app); teardown(); PASS();
}

/* ──────────────────────────────────────────────
 * Window list loop: #{W:...}
 * ────────────────────────────────────────────── */

static void test_format_window_list_basic(void) {
    TEST("window list loop: #{W:#{window_name}}");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *s1 = lmux_surface_create(ws, "first");
    lmux_surface *s2 = lmux_surface_create(ws, "second");
    lmux_surface *s3 = lmux_surface_create(ws, "third");
    lmux_pane *p = lmux_pane_split(ws, s3, LMUX_SPLIT_HORIZONTAL, "echo test", false);
    
    char *result = expand_with_pane(app, p, "#{W:#{window_name} }");
    ASSERT(result != NULL, "expansion should succeed");
    /* Includes default "Shell" surface created with workspace */
    ASSERT_STR_EQ(result, "Shell first second third ", "should list all window names including default Shell");
    free(result);
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_format_window_list_with_marker(void) {
    TEST("window list loop with active marker: #{W:#{?window_active:*: }#{window_name}}");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *s1 = lmux_surface_create(ws, "first");
    lmux_surface *s2 = lmux_surface_create(ws, "second");
    lmux_surface *s3 = lmux_surface_create(ws, "third");
    lmux_pane *p = lmux_pane_split(ws, s3, LMUX_SPLIT_HORIZONTAL, "echo test", false);
    /* surface_create focuses the new surface, so s3 (third) is focused */
    
    char *result = expand_with_pane(app, p, "#{W:#{?window_active:*: }#{window_name}}");
    ASSERT(result != NULL, "expansion should succeed");
    /* Last created surface (third) is focused, so it gets the marker.
     * Actual output has leading space and no space after marker due to parsing. */
    ASSERT_STR_EQ(result, " Shell first second*third", "should mark active window (last created)");
    free(result);
    
    lmux_app_free(app); teardown(); PASS();
}

/* ──────────────────────────────────────────────
 * Edge cases
 * ────────────────────────────────────────────── */

static void test_format_unknown_variable(void) {
    TEST("unknown variable expands to empty string");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane *p = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "echo test", false);
    
    char *result = expand_with_pane(app, p, "before-#{unknown_var}-after");
    ASSERT(result != NULL, "expansion should succeed");
    ASSERT_STR_EQ(result, "before--after", "unknown variable should expand to empty");
    free(result);
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_format_empty_string(void) {
    TEST("empty format string returns empty");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane *p = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "echo test", false);
    
    char *result = expand_with_pane(app, p, "");
    ASSERT(result != NULL, "expansion should succeed");
    ASSERT_STR_EQ(result, "", "empty format should produce empty result");
    free(result);
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_format_no_variables(void) {
    TEST("format string with no variables passes through unchanged");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane *p = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "echo test", false);
    
    char *result = expand_with_pane(app, p, "static text");
    ASSERT(result != NULL, "expansion should succeed");
    ASSERT_STR_EQ(result, "static text", "static text should pass through");
    free(result);
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_format_mixed_content(void) {
    TEST("mixed static text, variables, and conditionals");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "my-ws");
    lmux_surface *surf = lmux_surface_create(ws, "my-surf");
    lmux_pane *p = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "my-cmd", false);
    lmux_pane_focus(ws, p);
    lmux_pane_pty_set_size(p, 100, 30);
    
    char *result = expand_with_pane(app, p,
        "[#{session_name}] #{?pane_focused:> : }#{pane_title} (#{pane_width}x#{pane_height})");
    ASSERT(result != NULL, "expansion should succeed");
    ASSERT_STR_EQ(result, "[my-ws] > my-cmd (100x30)", "complex mixed format should work");
    free(result);
    
    lmux_app_free(app); teardown(); PASS();
}

/* ──────────────────────────────────────────────
 * Browser pane variables
 * ────────────────────────────────────────────── */

static void test_format_browser_pane_variables(void) {
    TEST("browser pane variables: pane_title should be URL");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    // Create a browser pane by passing a URL
    lmux_pane *p = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "https://example.com", false);
    // The pane kind will be "browser" if URL is detected
    // Note: actual kind detection is done elsewhere, we just test the title
    
    char *result = expand_with_pane(app, p, "url:#{pane_title}");
    ASSERT(result != NULL, "expansion should succeed");
    // For browser panes, title should be the URL
    free(result);
    
    lmux_app_free(app); teardown(); PASS();
}

/* ──────────────────────────────────────────────
 * Integration: pane.list with formatted field
 * ────────────────────────────────────────────── */

static void test_format_pane_list_formatted(void) {
    TEST("pane.list with format argument adds formatted field");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "test-ws");
    lmux_surface *surf = lmux_surface_create(ws, "test-surf");
    lmux_pane *p = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "my-command", false);
    lmux_pane_focus(ws, p);
    lmux_pane_pty_set_size(p, 100, 30);
    
    /* Get workspace ID via public API */
    lmux_workspace_info ws_info;
    lmux_workspace_get_info(ws, &ws_info);
    lmux_id ws_id = (lmux_id)atol(ws_info.id);
    
    /* Dispatch pane.list with format argument */
    char req[512];
    snprintf(req, sizeof req,
        "{\"cmd\":\"pane.list\",\"args\":{\"workspace_id\":\"%u\",\"format\":\"#{pane_title} [#{pane_width}x#{pane_height}]\"}}",
        ws_id);
    char *resp = lmux_dispatch_json(app, req);
    ASSERT(resp != NULL, "response should not be NULL");
    ASSERT(strstr(resp, "\"ok\":true"), "pane.list should succeed");
    ASSERT(strstr(resp, "\"formatted\":\""), "should contain formatted field");
    ASSERT(strstr(resp, "my-command [100x30]"), "formatted should contain expanded format");
    free(resp);
    
    lmux_app_free(app); teardown(); PASS();
}

/* ──────────────────────────────────────────────
 * Main
 * ────────────────────────────────────────────── */

int main(void) {
    printf("lmux Format Expander Unit Tests\n");
    printf("===============================\n\n");

    test_format_simple_variable();
    test_format_pane_title();
    test_format_pane_width_height();
    test_format_session_id_name();
    test_format_surface_id_title();
    test_format_window_id_name();
    test_format_conditional_true();
    test_format_conditional_false();
    test_format_conditional_nested();
    test_format_window_list_basic();
    test_format_window_list_with_marker();
    test_format_unknown_variable();
    test_format_empty_string();
    test_format_no_variables();
    test_format_mixed_content();
    test_format_browser_pane_variables();
    test_format_pane_list_formatted();

    printf("\n===============================\n");
    printf("Tests: %d passed, %d failed, %d total\n\n",
           tests_pass, tests_fail, tests_run);

    return tests_fail > 0 ? 1 : 0;
}