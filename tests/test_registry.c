/**
 * test_registry.c — Unit tests for the lmux command registry (M1a).
 *
 * The registry is table-driven and additive: it describes the commands the
 * daemon can dispatch without replacing the dispatch chain itself.
 */
#define LMUX_INTERNAL
#include "lmux.h"
#include <stdio.h>
#include <string.h>

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

static void test_all_returns_nonempty_table(void) {
    TEST("registry_all returns a non-empty table");
    size_t count = 0;
    const lmux_cmd_entry *entries = lmux_registry_all(&count);
    ASSERT(entries != NULL, "registry_all must return a non-NULL pointer");
    ASSERT(count > 0, "registry_all must report a non-zero count");
    PASS();
}

static void test_get_known_command(void) {
    TEST("registry_get finds workspace.create");
    const lmux_cmd_entry *e = lmux_registry_get("workspace.create");
    ASSERT(e != NULL, "workspace.create must be present in the registry");
    ASSERT(strcmp(e->name, "workspace.create") == 0, "entry name must match");
    ASSERT(e->summary != NULL && e->summary[0] != '\0',
           "entry summary must be non-empty");
    PASS();
}

static void test_get_list_commands(void) {
    TEST("registry_get finds list-commands");
    const lmux_cmd_entry *e = lmux_registry_get("list-commands");
    ASSERT(e != NULL, "list-commands must be present in the registry");
    ASSERT(strcmp(e->name, "list-commands") == 0, "entry name must match");
    PASS();
}

static void test_get_unknown_command(void) {
    TEST("registry_get returns NULL for unknown command");
    ASSERT(lmux_registry_get("does-not-exist") == NULL,
           "unknown command must return NULL");
    ASSERT(lmux_registry_get(NULL) == NULL, "NULL name must return NULL");
    PASS();
}

/* ──────────────────────────────────────────────
 * Main
 * ────────────────────────────────────────────── */

int main(void) {
    printf("lmux Command Registry Unit Tests\n");
    printf("=================================\n\n");

    test_all_returns_nonempty_table();
    test_get_known_command();
    test_get_list_commands();
    test_get_unknown_command();

    printf("\n=================================\n");
    printf("Tests: %d passed, %d failed, %d total\n\n",
           tests_pass, tests_fail, tests_run);

    return tests_fail > 0 ? 1 : 0;
}
