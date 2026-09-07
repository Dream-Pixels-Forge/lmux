/*
 * test_browser.c — Unit tests for the browser module.
 *
 * Tests browser lifecycle, navigation, interaction, and JavaScript evaluation.
 * Note: Full WebKit rendering requires a display server (X11/Wayland).
 * These tests verify the API surface and error handling.
 */

#define _POSIX_C_SOURCE 200809L
#include "lmux_browser.h"

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int tests_run = 0;
static int tests_passed = 0;

#define TEST(name) static void name(void)
#define RUN(name) do { \
    tests_run++; \
    printf("  %-40s ", #name); \
    name(); \
    tests_passed++; \
    printf("PASS\n"); \
} while(0)

#define ASSERT(cond) do { \
    if (!(cond)) { \
        printf("FAIL\n    %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        exit(1); \
    } \
} while(0)

#define ASSERT_NULL(ptr) ASSERT((ptr) == NULL)
#define ASSERT_NOT_NULL(ptr) ASSERT((ptr) != NULL)

/* ------------------------------------------------------------------ */
/* Lifecycle tests                                                     */
/* ------------------------------------------------------------------ */

TEST(test_browser_new_free) {
    lmux_browser *b = lmux_browser_new();
    /* May return NULL if no display server — that's OK for headless */
    if (b) {
        lmux_browser_free(b);
    }
    /* Just verify API doesn't crash */
}

TEST(test_browser_free_null) {
    /* Should not crash */
    lmux_browser_free(NULL);
}

/* ------------------------------------------------------------------ */
/* Navigation tests (require display)                                   */
/* ------------------------------------------------------------------ */

TEST(test_browser_navigate_null) {
    ASSERT(!lmux_browser_navigate(NULL, "https://example.com"));
    ASSERT(!lmux_browser_navigate((lmux_browser *)0x1, NULL));
}

TEST(test_browser_go_back_null) {
    ASSERT(!lmux_browser_go_back(NULL));
}

TEST(test_browser_go_forward_null) {
    ASSERT(!lmux_browser_go_forward(NULL));
}

TEST(test_browser_reload_null) {
    ASSERT(!lmux_browser_reload(NULL));
}

TEST(test_browser_stop_null) {
    ASSERT(!lmux_browser_stop(NULL));
}

/* ------------------------------------------------------------------ */
/* Content access tests (null safety)                                   */
/* ------------------------------------------------------------------ */

TEST(test_browser_get_url_null) {
    ASSERT_NULL(lmux_browser_get_url(NULL));
}

TEST(test_browser_get_title_null) {
    ASSERT_NULL(lmux_browser_get_title(NULL));
}

TEST(test_browser_snapshot_null) {
    ASSERT_NULL(lmux_browser_get_snapshot(NULL));
}

TEST(test_browser_get_html_null) {
    ASSERT_NULL(lmux_browser_get_html(NULL));
}

/* ------------------------------------------------------------------ */
/* Interaction tests (null safety)                                      */
/* ------------------------------------------------------------------ */

TEST(test_browser_click_null) {
    ASSERT(!lmux_browser_click(NULL, "button"));
    ASSERT(!lmux_browser_click((lmux_browser *)0x1, NULL));
}

TEST(test_browser_fill_null) {
    ASSERT(!lmux_browser_fill(NULL, "input", "value"));
    ASSERT(!lmux_browser_fill((lmux_browser *)0x1, NULL, "value"));
    ASSERT(!lmux_browser_fill((lmux_browser *)0x1, "input", NULL));
}

TEST(test_browser_select_null) {
    ASSERT(!lmux_browser_select(NULL, "select", "option"));
}

TEST(test_browser_press_key_null) {
    ASSERT(!lmux_browser_press_key(NULL, "Enter"));
}

/* ------------------------------------------------------------------ */
/* JavaScript tests (null safety)                                       */
/* ------------------------------------------------------------------ */

TEST(test_browser_evaluate_null) {
    ASSERT_NULL(lmux_browser_evaluate(NULL, "1+1"));
    ASSERT_NULL(lmux_browser_evaluate((lmux_browser *)0x1, NULL));
}

/* ------------------------------------------------------------------ */
/* Screenshot tests (null safety)                                       */
/* ------------------------------------------------------------------ */

TEST(test_browser_screenshot_null) {
    ASSERT_NULL(lmux_browser_screenshot(NULL, "/tmp/test.png"));
}

/* ------------------------------------------------------------------ */
/* State tests (null safety)                                            */
/* ------------------------------------------------------------------ */

TEST(test_browser_is_loading_null) {
    ASSERT(!lmux_browser_is_loading(NULL));
}

TEST(test_browser_get_progress_null) {
    ASSERT(lmux_browser_get_progress(NULL) == 0.0);
}

/* ------------------------------------------------------------------ */
/* Main                                                                */
/* ------------------------------------------------------------------ */

int main(void) {
    printf("Running browser unit tests...\n");

    RUN(test_browser_new_free);
    RUN(test_browser_free_null);
    RUN(test_browser_navigate_null);
    RUN(test_browser_go_back_null);
    RUN(test_browser_go_forward_null);
    RUN(test_browser_reload_null);
    RUN(test_browser_stop_null);
    RUN(test_browser_get_url_null);
    RUN(test_browser_get_title_null);
    RUN(test_browser_snapshot_null);
    RUN(test_browser_get_html_null);
    RUN(test_browser_click_null);
    RUN(test_browser_fill_null);
    RUN(test_browser_select_null);
    RUN(test_browser_press_key_null);
    RUN(test_browser_evaluate_null);
    RUN(test_browser_screenshot_null);
    RUN(test_browser_is_loading_null);
    RUN(test_browser_get_progress_null);

    printf("\n✓ %d/%d browser tests passed\n", tests_passed, tests_run);
    return 0;
}
