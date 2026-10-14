/**
 * test_buffer.c — Unit tests for lmux named paste buffers.
 *
 * Tests match the current lmux_buffer API.
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

/* Buffer lifecycle */
static void test_buffer_set_and_get(void) {
    TEST("buffer_set creates a buffer and buffer_get retrieves it");
    lmux_app *app = make_app();
    
    const char *data = "hello world";
    int rc = lmux_buffer_set(app, "test-buffer", data, strlen(data));
    ASSERT(rc == 0, "buffer_set should succeed");
    
    char out[256] = {0};
    size_t len = lmux_buffer_get(app, "test-buffer", out, sizeof out);
    ASSERT(len == strlen(data), "buffer_get should return correct length");
    ASSERT(strcmp(out, data) == 0, "buffer content should match");
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_buffer_auto_naming(void) {
    TEST("buffer_set auto-generates buffer0001, buffer0002... names when name is NULL");
    lmux_app *app = make_app();
    
    int rc1 = lmux_buffer_set(app, NULL, "first", 5);
    ASSERT(rc1 == 0, "first buffer_set should succeed");
    
    int rc2 = lmux_buffer_set(app, NULL, "second", 6);
    ASSERT(rc2 == 0, "second buffer_set should succeed");
    
    int rc3 = lmux_buffer_set(app, NULL, "third", 5);
    ASSERT(rc3 == 0, "third buffer_set should succeed");
    
    /* Check auto-generated names exist */
    char out[256];
    ASSERT(lmux_buffer_get(app, "buffer0001", out, sizeof out) == 5, "buffer0001 should exist");
    ASSERT(strcmp(out, "first") == 0, "buffer0001 content should match");
    ASSERT(lmux_buffer_get(app, "buffer0002", out, sizeof out) == 6, "buffer0002 should exist");
    ASSERT(strcmp(out, "second") == 0, "buffer0002 content should match");
    ASSERT(lmux_buffer_get(app, "buffer0003", out, sizeof out) == 5, "buffer0003 should exist");
    ASSERT(strcmp(out, "third") == 0, "buffer0003 content should match");
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_buffer_list(void) {
    TEST("buffer_list returns all buffer names");
    lmux_app *app = make_app();
    
    lmux_buffer_set(app, "alpha", "aaa", 3);
    lmux_buffer_set(app, "beta", "bbb", 3);
    lmux_buffer_set(app, "gamma", "ccc", 3);
    
    char *names[16];
    size_t count = lmux_buffer_list(app, names, 16);
    ASSERT(count == 3, "buffer_list should return 3 buffers");
    
    /* Verify all names present (order may vary) */
    bool found_alpha = false, found_beta = false, found_gamma = false;
    for (size_t i = 0; i < count; i++) {
        if (strcmp(names[i], "alpha") == 0) found_alpha = true;
        else if (strcmp(names[i], "beta") == 0) found_beta = true;
        else if (strcmp(names[i], "gamma") == 0) found_gamma = true;
        free(names[i]);
    }
    ASSERT(found_alpha && found_beta && found_gamma, "all buffer names should be listed");
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_buffer_delete(void) {
    TEST("buffer_delete removes a buffer");
    lmux_app *app = make_app();
    
    lmux_buffer_set(app, "to-delete", "data", 4);
    ASSERT(lmux_buffer_get(app, "to-delete", NULL, 0) == 4, "buffer should exist before delete");
    
    bool rc = lmux_buffer_delete(app, "to-delete");
    ASSERT(rc == true, "buffer_delete should return true");
    ASSERT(lmux_buffer_get(app, "to-delete", NULL, 0) == 0, "buffer should not exist after delete");
    
    /* Deleting non-existent buffer returns false */
    rc = lmux_buffer_delete(app, "nonexistent");
    ASSERT(rc == false, "buffer_delete on missing should return false");
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_buffer_limit_eviction(void) {
    TEST("buffer_limit evicts oldest buffers when exceeded (FIFO)");
    lmux_app *app = make_app();
    
    /* Set limit to 3 */
    lmux_buffer_set_limit(app, 3);
    
    lmux_buffer_set(app, "buf1", "one", 3);
    lmux_buffer_set(app, "buf2", "two", 3);
    lmux_buffer_set(app, "buf3", "three", 5);
    ASSERT(lmux_buffer_count(app) == 3, "should have 3 buffers");
    
    /* Add 4th buffer - should evict buf1 */
    lmux_buffer_set(app, "buf4", "four", 4);
    ASSERT(lmux_buffer_count(app) == 3, "should still have 3 buffers after limit");
    ASSERT(lmux_buffer_get(app, "buf1", NULL, 0) == 0, "buf1 should be evicted");
    ASSERT(lmux_buffer_get(app, "buf2", NULL, 0) == 3, "buf2 should remain");
    ASSERT(lmux_buffer_get(app, "buf3", NULL, 0) == 5, "buf3 should remain");
    ASSERT(lmux_buffer_get(app, "buf4", NULL, 0) == 4, "buf4 should exist");
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_buffer_limit_zero_means_unlimited(void) {
    TEST("buffer_limit of 0 means unlimited");
    lmux_app *app = make_app();
    
    lmux_buffer_set_limit(app, 0);
    
    for (int i = 0; i < 100; i++) {
        char name[32], data[32];
        snprintf(name, sizeof name, "buf%d", i);
        snprintf(data, sizeof data, "data%d", i);
        lmux_buffer_set(app, name, data, strlen(data));
    }
    ASSERT(lmux_buffer_count(app) == 100, "should have 100 buffers with limit 0");
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_buffer_save_load_file(void) {
    TEST("buffer_save writes buffer to file and buffer_load reads it back");
    lmux_app *app = make_app();
    
    const char *data = "file buffer content";
    lmux_buffer_set(app, "filebuf", data, strlen(data));
    
    char path[256];
    snprintf(path, sizeof path, "/tmp/lmux-test-buffer-%d.txt", getpid());
    
    bool rc = lmux_buffer_save(app, "filebuf", path);
    ASSERT(rc == true, "buffer_save should succeed");
    
    /* Verify file content */
    FILE *f = fopen(path, "r");
    ASSERT(f != NULL, "saved file should exist");
    char file_content[256] = {0};
    size_t n = fread(file_content, 1, sizeof file_content - 1, f);
    fclose(f);
    ASSERT(n == strlen(data), "file should have correct size");
    ASSERT(strcmp(file_content, data) == 0, "file content should match");
    
    /* Load into different buffer name */
    rc = lmux_buffer_load(app, "loadedbuf", path);
    ASSERT(rc == true, "buffer_load should succeed");
    ASSERT(lmux_buffer_get(app, "loadedbuf", NULL, 0) == strlen(data), "loaded buffer should have correct size");
    
    char out[256];
    lmux_buffer_get(app, "loadedbuf", out, sizeof out);
    ASSERT(strcmp(out, data) == 0, "loaded buffer content should match");
    
    unlink(path);
    lmux_app_free(app); teardown(); PASS();
}

static void test_buffer_save_nonexistent_fails(void) {
    TEST("buffer_save on non-existent buffer returns false");
    lmux_app *app = make_app();
    
    char path[256];
    snprintf(path, sizeof path, "/tmp/lmux-test-buffer-%d.txt", getpid());
    
    bool rc = lmux_buffer_save(app, "nonexistent", path);
    ASSERT(rc == false, "buffer_save on missing buffer should fail");
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_buffer_load_overwrites(void) {
    TEST("buffer_load overwrites existing buffer with same name");
    lmux_app *app = make_app();
    
    lmux_buffer_set(app, "same", "original", 8);
    
    char path[256];
    snprintf(path, sizeof path, "/tmp/lmux-test-buffer-%d.txt", getpid());
    FILE *f = fopen(path, "w");
    fprintf(f, "new content");
    fclose(f);
    
    bool rc = lmux_buffer_load(app, "same", path);
    ASSERT(rc == true, "buffer_load should succeed");
    
    char out[256];
    size_t len = lmux_buffer_get(app, "same", out, sizeof out);
    ASSERT(len == 11, "loaded buffer should have new size");
    ASSERT(strcmp(out, "new content") == 0, "loaded buffer should have new content");
    
    unlink(path);
    lmux_app_free(app); teardown(); PASS();
}

static void test_format_variables_buffer_count(void) {
    TEST("format variable #{buffer_count} returns buffer count");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane *pane = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, NULL, false);
    
    lmux_buffer_set(app, "buf1", "one", 3);
    lmux_buffer_set(app, "buf2", "two", 3);
    
    char fmt[] = "#{buffer_count}";
    char out[64] = {0};
    bool ok = lmux_format_expand(app, pane, fmt, out, sizeof out);
    ASSERT(ok, "format_expand should succeed");
    ASSERT(strcmp(out, "2") == 0, "buffer_count should be 2");
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_format_variables_buffer_size(void) {
    TEST("format variable #{buffer_<name>_size} returns buffer size");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane *pane = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, NULL, false);
    
    lmux_buffer_set(app, "mybuf", "hello", 5);
    
    char fmt[] = "#{buffer_mybuf_size}";
    char out[64] = {0};
    bool ok = lmux_format_expand(app, pane, fmt, out, sizeof out);
    ASSERT(ok, "format_expand should succeed");
    ASSERT(strcmp(out, "5") == 0, "buffer_mybuf_size should be 5");
    
    /* Non-existent buffer returns empty */
    char fmt2[] = "#{buffer_nonexistent_size}";
    char out2[64] = {0};
    ok = lmux_format_expand(app, pane, fmt2, out2, sizeof out2);
    ASSERT(ok, "format_expand should succeed");
    ASSERT(strcmp(out2, "") == 0, "non-existent buffer size should be empty");
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_buffer_notifications_created(void) {
    TEST("buffer_set emits %buffer-created notification");
    lmux_app *app = make_app();
    
    /* We can't easily test control notifications in unit test without a control client,
     * but we can verify the internal event is pushed */
    lmux_buffer_set(app, "notify-test", "data", 4);
    
    /* Check event queue for buffer-created event */
    size_t pending = lmux_events_pending(app);
    ASSERT(pending > 0, "should have events pending");
    
    char *event = lmux_event_pop(app);
    ASSERT(event != NULL, "should pop an event");
    ASSERT(strstr(event, "buffer-created") != NULL, "event should be buffer-created");
    ASSERT(strstr(event, "notify-test") != NULL, "event should contain buffer name");
    free(event);
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_buffer_notifications_deleted(void) {
    TEST("buffer_delete emits %buffer-deleted notification");
    lmux_app *app = make_app();
    
    lmux_buffer_set(app, "to-delete", "data", 4);
    lmux_event_pop(app); /* consume buffer-created */
    
    lmux_buffer_delete(app, "to-delete");
    
    size_t pending = lmux_events_pending(app);
    ASSERT(pending > 0, "should have events pending");
    
    char *event = lmux_event_pop(app);
    ASSERT(event != NULL, "should pop an event");
    ASSERT(strstr(event, "buffer-deleted") != NULL, "event should be buffer-deleted");
    ASSERT(strstr(event, "to-delete") != NULL, "event should contain buffer name");
    free(event);
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_buffer_notifications_changed(void) {
    TEST("buffer_set on existing buffer emits %buffer-changed notification");
    lmux_app *app = make_app();
    
    lmux_buffer_set(app, "existing", "old", 3);
    lmux_event_pop(app); /* consume buffer-created */
    
    lmux_buffer_set(app, "existing", "new content", 11);
    
    size_t pending = lmux_events_pending(app);
    ASSERT(pending > 0, "should have events pending");
    
    char *event = lmux_event_pop(app);
    ASSERT(event != NULL, "should pop an event");
    ASSERT(strstr(event, "buffer-changed") != NULL, "event should be buffer-changed");
    ASSERT(strstr(event, "existing") != NULL, "event should contain buffer name");
    free(event);
    
    lmux_app_free(app); teardown(); PASS();
}

static void test_buffer_clear_on_app_free(void) {
    TEST("buffers are freed when app is freed");
    lmux_app *app = make_app();
    
    lmux_buffer_set(app, "buf1", "data", 4);
    lmux_buffer_set(app, "buf2", "data", 4);
    ASSERT(lmux_buffer_count(app) == 2, "should have 2 buffers");
    
    lmux_app_free(app); /* This should free all buffers */
    teardown();
    
    /* No crash = PASS */
    PASS();
}

int main(void) {
    test_buffer_set_and_get();
    test_buffer_auto_naming();
    test_buffer_list();
    test_buffer_delete();
    test_buffer_limit_eviction();
    test_buffer_limit_zero_means_unlimited();
    test_buffer_save_load_file();
    test_buffer_save_nonexistent_fails();
    test_buffer_load_overwrites();
    test_format_variables_buffer_count();
    test_format_variables_buffer_size();
    test_buffer_notifications_created();
    test_buffer_notifications_deleted();
    test_buffer_notifications_changed();
    test_buffer_clear_on_app_free();
    
    printf("\n=== Buffer Tests: %d run, %d pass, %d fail ===\n", tests_run, tests_pass, tests_fail);
    return tests_fail == 0 ? 0 : 1;
}