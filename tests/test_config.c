/**
 * test_config.c — Unit tests for lmux config loader/validator.
 *
 * Tests match the current lmux_config struct and API.
 */
#define LMUX_INTERNAL
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

static void write_test_config(const char *path, const char *content) {
    FILE *f = fopen(path, "w");
    ASSERT(f != NULL, "fopen should succeed");
    fprintf(f, "%s", content);
    fclose(f);
}

/* ──────────────────────────────────────────────
 * Basic load
 * ────────────────────────────────────────────── */

static void test_load_valid_json(void) {
    TEST("load valid config.json");
    char path[128];
    snprintf(path, sizeof path, "/tmp/lmux-test-config-%d.json", getpid());

    write_test_config(path, "{"
        "\"font_family\": \"Fira Code\","
        "\"font_size\": 14,"
        "\"theme\": \"light\""
    "}");

    lmux_config *cfg = lmux_config_new();
    bool ok = lmux_config_load(cfg, path);
    ASSERT(ok, "loading valid config should succeed");
    ASSERT(strcmp(cfg->font_family, "Fira Code") == 0, "font_family should match");
    ASSERT(cfg->font_size == 14, "font_size should match");
    ASSERT(strcmp(cfg->theme, "light") == 0, "theme should match");

    lmux_config_free(cfg);
    unlink(path);
    PASS();
}

static void test_load_malformed_json(void) {
    TEST("load malformed JSON — missing closing brace");
    char path[128];
    snprintf(path, sizeof path, "/tmp/lmux-test-config-%d.json", getpid());

    write_test_config(path, "{\"font_family\": \"monospace\"");

    lmux_config *cfg = lmux_config_new();
    bool ok = lmux_config_load(cfg, path);
    ASSERT(ok == false, "malformed JSON should return false");
    /* Defaults should be preserved */
    ASSERT(strcmp(cfg->font_family, "monospace") == 0, "defaults preserved");

    lmux_config_free(cfg);
    unlink(path);
    PASS();
}

static void test_load_empty_json(void) {
    TEST("load minimal '{}' config");
    char path[128];
    snprintf(path, sizeof path, "/tmp/lmux-test-config-%d.json", getpid());

    write_test_config(path, "{}");

    lmux_config *cfg = lmux_config_new();
    bool ok = lmux_config_load(cfg, path);
    ASSERT(ok, "empty object should succeed");
    ASSERT(strcmp(cfg->font_family, "monospace") == 0, "default font family");
    ASSERT(cfg->font_size == 12, "default font size");

    lmux_config_free(cfg);
    unlink(path);
    PASS();
}

static void test_load_invalid_types(void) {
    TEST("load config with wrong types (string instead of int)");
    char path[128];
    snprintf(path, sizeof path, "/tmp/lmux-test-config-%d.json", getpid());

    write_test_config(path, "{"
        "\"font_size\": \"not-a-number\""
    "}");

    lmux_config *cfg = lmux_config_new();
    lmux_config_load(cfg, path);
    /* Should still load but font_size stays at default (no crash) */
    ASSERT(cfg->font_size == 12, "font_size should use default on type mismatch");

    lmux_config_free(cfg);
    unlink(path);
    PASS();
}

/* ──────────────────────────────────────────────
 * Keybindings
 * ────────────────────────────────────────────── */

static void test_load_keybindings(void) {
    TEST("load config with keybindings");
    char path[128];
    snprintf(path, sizeof path, "/tmp/lmux-test-config-%d.json", getpid());

    write_test_config(path, "{"
        "\"keybindings\": {"
        "\"split-v\": \"C-b percent\","
        "\"split-h\": \"C-b quote\""
        "}"
    "}");

    lmux_config *cfg = lmux_config_new();
    bool ok = lmux_config_load(cfg, path);
    ASSERT(ok, "config with keybindings should load");
    ASSERT(cfg->keybindings.len == 2, "should have 2 keybindings");

    lmux_keybinding *kb = lmux_config_find_key(cfg, "split-v");
    ASSERT(kb != NULL, "should find 'split-v' keybinding");
    ASSERT(strcmp(kb->action, "C-b percent") == 0, "action string should match");

    lmux_config_free(cfg);
    unlink(path);
    PASS();
}

/* ──────────────────────────────────────────────
 * Workspace groups
 * ────────────────────────────────────────────── */

static void test_load_groups(void) {
    TEST("load config with workspace groups");
    char path[128];
    snprintf(path, sizeof path, "/tmp/lmux-test-config-%d.json", getpid());

    write_test_config(path, "{"
        "\"groups\": {"
        "\"dev\": [\"1\", \"2\"],"
        "\"ops\": [\"3\"]"
        "}"
    "}");

    lmux_config *cfg = lmux_config_new();
    bool ok = lmux_config_load(cfg, path);
    ASSERT(ok, "config with groups should load");
    ASSERT(cfg->workspace_groups.len > 0, "should have groups");

    lmux_workspace_group *g = lmux_workspace_group_find(cfg, "dev");
    ASSERT(g != NULL, "should find 'dev' group");
    ASSERT(g->workspace_ids.len == 2, "'dev' group should have 2 workspace IDs");

    lmux_config_free(cfg);
    unlink(path);
    PASS();
}

/* ──────────────────────────────────────────────
 * Group create/find round-trip
 * ────────────────────────────────────────────── */

static void test_group_create_and_find(void) {
    TEST("workspace_group_create and find round-trip");
    lmux_config *cfg = lmux_config_new();

    lmux_workspace_group *g = lmux_workspace_group_create(cfg, "test-group");
    ASSERT(g != NULL, "group_create should succeed");
    ASSERT(strcmp(g->name, "test-group") == 0, "name should match");

    lmux_workspace_group *found = lmux_workspace_group_find(cfg, "test-group");
    ASSERT(found == g, "find should return the same group");

    lmux_config_free(cfg);
    PASS();
}

/* ──────────────────────────────────────────────
 * Main
 * ────────────────────────────────────────────── */

int main(void) {
    printf("lmux Config Unit Tests\n");
    printf("======================\n\n");

    test_load_valid_json();
    test_load_malformed_json();
    test_load_empty_json();
    test_load_invalid_types();
    test_load_keybindings();
    test_load_groups();
    test_group_create_and_find();

    printf("\n======================\n");
    printf("Tests: %d passed, %d failed, %d total\n\n",
           tests_pass, tests_fail, tests_run);

    return tests_fail > 0 ? 1 : 0;
}
