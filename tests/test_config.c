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
 * Round-trip tests (RED before F2/F3)
 *
 * The three tests above each feed the loader a hand-written
 * shape that the loader happens to accept. None of them ever
 * exercises what lmux_config_save() actually writes, which is
 * how all three collections came to be write-only. These tests
 * use the real save format and a real temp file.
 * ────────────────────────────────────────────── */

static void test_keybindings_save_format_loads(void) {
    TEST("keybindings in save format (array) load correctly");
    char path[128];
    snprintf(path, sizeof path, "/tmp/lmux-test-kb-rt-%d.json", getpid());

    /* Exactly what lmux_config_save() emits for keybindings. */
    write_test_config(path, "{"
        "\"keybindings\": ["
          "{\"key\": \"split-v\", \"action\": \"C-b percent\"},"
          "{\"key\": \"split-h\", \"action\": \"C-b quote\"}"
        "]"
    "}");

    lmux_config *cfg = lmux_config_new();
    bool ok = lmux_config_load(cfg, path);
    ASSERT(ok, "config with save-format keybindings should load");
    ASSERT(cfg->keybindings.len == 2, "should parse 2 keybindings from save format");

    lmux_keybinding *kb = lmux_config_find_key(cfg, "split-v");
    ASSERT(kb != NULL, "should find 'split-v' from save format");
    ASSERT(strcmp(kb->action, "C-b percent") == 0, "action should match");

    lmux_config_free(cfg);
    unlink(path);
    PASS();
}

static void test_load_themes(void) {
    TEST("themes array from save format loads");
    char path[128];
    snprintf(path, sizeof path, "/tmp/lmux-test-themes-%d.json", getpid());

    /* Exactly what lmux_config_save() emits for themes. */
    write_test_config(path, "{"
        "\"themes\": ["
          "{\"name\": \"dracula\", \"fg\": \"#f8f8f2\", \"bg\": \"#282a36\", \"cursor\": \"#f8f8f2\"},"
          "{\"name\": \"solarized\", \"fg\": \"#839496\", \"bg\": \"#002b36\", \"cursor\": \"#93a1a1\"}"
        "]"
    "}");

    lmux_config *cfg = lmux_config_new();
    bool ok = lmux_config_load(cfg, path);
    ASSERT(ok, "config with themes should load");
    ASSERT(cfg->themes.len == 2, "should parse 2 themes");

    lmux_theme_entry *t = lmux_config_find_theme(cfg, "dracula");
    ASSERT(t != NULL, "should find 'dracula' theme");
    ASSERT(strcmp(t->bg, "#282a36") == 0, "dracula bg should match");

    lmux_config_free(cfg);
    unlink(path);
    PASS();
}

static void test_groups_save_load_roundtrip(void) {
    TEST("workspace groups survive a real save/load round-trip");
    char path[128];
    snprintf(path, sizeof path, "/tmp/lmux-test-groups-rt-%d.json", getpid());

    lmux_config *cfg = lmux_config_new();
    lmux_workspace_group *g = lmux_workspace_group_create(cfg, "roundtrip");
    ASSERT(g != NULL, "group_create should succeed");
    ASSERT(lmux_workspace_group_add(cfg, "roundtrip", 7), "add ws 7");
    ASSERT(lmux_workspace_group_add(cfg, "roundtrip", 9), "add ws 9");
    ASSERT(lmux_config_save(cfg, path), "config_save should succeed");

    lmux_config *reloaded = lmux_config_new();
    ASSERT(lmux_config_load(reloaded, path), "reload should succeed");

    lmux_workspace_group *found = lmux_workspace_group_find(reloaded, "roundtrip");
    ASSERT(found != NULL, "group should survive save/load round-trip");
    ASSERT(found->workspace_ids.len == 2, "group should still hold 2 workspace ids");

    lmux_config_free(cfg);
    lmux_config_free(reloaded);
    unlink(path);
    PASS();
}

static void test_save_creates_deep_parent_dirs(void) {
    TEST("save creates missing parent directories at any depth");
    char base[128];
    snprintf(base, sizeof base, "/tmp/lmux-test-deep-%d", getpid());
    /* Three levels, none of which exist. mkdir() is not recursive, so a
     * single-level mkdir() of the leaf fails with ENOENT and the save
     * silently no-ops — the user sees success and gets no file. */
    char path[256];
    snprintf(path, sizeof path, "%s/a/b/c/config.json", base);

    lmux_config *cfg = lmux_config_new();
    ASSERT(lmux_config_save(cfg, path), "save should succeed with missing parents");

    lmux_config *reloaded = lmux_config_new();
    ASSERT(lmux_config_load(reloaded, path), "file should exist and load back");
    ASSERT(strcmp(reloaded->font_family, cfg->font_family) == 0,
           "reloaded values should match");

    lmux_config_free(cfg);
    lmux_config_free(reloaded);
    /* rm -rf the tree; rmdir on the leaf chain would need four calls */
    char rm[320];
    snprintf(rm, sizeof rm, "rm -rf %s", base);
    if (system(rm) != 0) { /* best effort cleanup */ }
    PASS();
}

static void test_save_reuses_existing_parent_dir(void) {
    TEST("save reuses an existing parent directory");
    char path[256];
    snprintf(path, sizeof path, "/tmp/lmux-test-existing-%d/config.json", getpid());

    lmux_config *cfg = lmux_config_new();
    /* First save creates the directory; second must succeed identically. */
    ASSERT(lmux_config_save(cfg, path), "first save should create the directory");
    ASSERT(lmux_config_save(cfg, path), "second save should reuse it");

    lmux_config *reloaded = lmux_config_new();
    ASSERT(lmux_config_load(reloaded, path), "file should load back");

    lmux_config_free(cfg);
    lmux_config_free(reloaded);
    char rm[320];
    snprintf(rm, sizeof rm, "rm -rf /tmp/lmux-test-existing-%d", getpid());
    if (system(rm) != 0) { /* best effort cleanup */ }
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
    test_keybindings_save_format_loads();
    test_load_themes();
    test_groups_save_load_roundtrip();
    test_save_creates_deep_parent_dirs();
    test_save_reuses_existing_parent_dir();

    printf("\n======================\n");
    printf("Tests: %d passed, %d failed, %d total\n\n",
           tests_pass, tests_fail, tests_run);

    return tests_fail > 0 ? 1 : 0;
}
