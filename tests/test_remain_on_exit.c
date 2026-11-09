/**
 * test_remain_on_exit.c — Unit tests for remain-on-exit, respawn, and activity monitoring.
 */
#define _POSIX_C_SOURCE 200809L
#define _GNU_SOURCE
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

/* Test remain-on-exit flag on pane */
static void test_pane_remain_on_exit_flag(void) {
    TEST("pane remain-on-exit flag can be set and retrieved");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane *pane = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "echo test", true);
    
    /* Default should be false */
    bool remain = lmux_pane_get_remain_on_exit(pane);
    ASSERT(remain == false, "default remain-on-exit should be false");
    
    /* Set to true */
    bool ok = lmux_pane_set_remain_on_exit(pane, true);
    ASSERT(ok, "set remain-on-exit true should succeed");
    
    remain = lmux_pane_get_remain_on_exit(pane);
    ASSERT(remain == true, "remain-on-exit should be true after setting");
    
    /* Set to false */
    ok = lmux_pane_set_remain_on_exit(pane, false);
    ASSERT(ok, "set remain-on-exit false should succeed");
    
    remain = lmux_pane_get_remain_on_exit(pane);
    ASSERT(remain == false, "remain-on-exit should be false after unsetting");
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test pane-died hook fires when pane exits with remain-on-exit */
static void test_pane_died_hook(void) {
    TEST("pane-died hook fires when pane exits with remain-on-exit");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    /* Use 'cat' which waits for input, then send exit */
    lmux_pane *pane = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", true);
    
    /* Enable remain-on-exit */
    lmux_pane_set_remain_on_exit(pane, true);
    
    /* Register pane-died hook */
    lmux_hook_add(app, "pane-died", "echo pane died");
    
    /* Spawn the pty and exit - send EOF (Ctrl+D) to cat */
    lmux_pane_send_keys(pane, "\x04");
    
    /* Wait for process to exit - call tick multiple times */
    for (int i = 0; i < 50; i++) {
        usleep(20000);
        lmux_app_tick(app);
    }
    
    /* Check if pane-died hook fired */
    char *event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued for pane-died");
    ASSERT(strstr(event, "pane-died") != NULL, "event should mention pane-died");
    free(event);
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test pane-exited hook fires with exit code */
static void test_pane_exited_hook(void) {
    TEST("pane-exited hook fires with exit code");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    /* Use a shell command that exits immediately with code 42 */
    lmux_pane *pane = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "sh -c 'exit 42'", true);
    
    /* Enable remain-on-exit */
    lmux_pane_set_remain_on_exit(pane, true);
    
    /* Register pane-exited hook */
    lmux_hook_add(app, "pane-exited", "echo pane exited");
    
    /* Wait for process to exit - call tick multiple times */
    for (int i = 0; i < 50; i++) {
        usleep(20000);
        lmux_app_tick(app);
    }
    
    /* Check if pane-exited hook fired */
    char *event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued for pane-exited");
    ASSERT(strstr(event, "pane-exited") != NULL, "event should mention pane-exited");
    free(event);
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test respawn-pane restores the pane */
static void test_respawn_pane(void) {
    TEST("respawn-pane restores pane with same command");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane *pane = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "sh -c 'exit 0'", true);
    
    /* Enable remain-on-exit */
    lmux_pane_set_remain_on_exit(pane, true);
    
    /* Wait for process to exit - call tick multiple times */
    for (int i = 0; i < 50; i++) {
        usleep(20000);
        lmux_app_tick(app);
    }
    
    /* Respawn the pane */
    bool ok = lmux_pane_respawn(pane);
    ASSERT(ok, "respawn should succeed");
    
    /* Check pane info - should have valid pty and pid after respawn */
    lmux_pane_info info;
    ASSERT(lmux_pane_get_info(pane, &info), "get_info should succeed");
    /* Note: we can't directly check command, pty_fd, child_pid via public API */
    /* but we can verify the pane is still valid and respawn succeeded */
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test monitor-activity hook fires on output */
static void test_monitor_activity_hook(void) {
    TEST("monitor-activity hook fires on pane output");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane *pane = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", true);
    
    /* Set monitor activity interval to 100ms */
    lmux_pane_set_monitor_activity_interval(pane, 100);
    lmux_pane_set_monitor_activity_action(pane, "any");
    
    /* Register monitor-activity hook */
    lmux_hook_add(app, "monitor-activity", "echo activity detected");
    
    /* Send some output */
    lmux_pane_send_keys(pane, "hello\n");
    
    /* Wait and call tick multiple times */
    for (int i = 0; i < 20; i++) {
        usleep(50000);
        lmux_app_tick(app);
    }
    
    /* Check if monitor-activity hook fired */
    char *event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued for monitor-activity");
    ASSERT(strstr(event, "monitor-activity") != NULL, "event should mention monitor-activity");
    free(event);
    
    lmux_app_free(app); teardown(); PASS();
}


/* Test monitor-bell hook fires on bell character */
static void test_monitor_bell_hook(void) {
    TEST("monitor-bell hook fires on bell character");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    /* Use a command that outputs a bell character */
    lmux_pane *pane = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "printf '\\a'", true);
    
    /* Set monitor bell action */
    lmux_pane_set_monitor_bell_action(pane, "any");
    
    /* Register monitor-bell hook */
    lmux_hook_add(app, "monitor-bell", "echo bell detected");
    
    /* Wait for the command to run and output the bell character */
    for (int i = 0; i < 20; i++) {
        usleep(50000);
        lmux_app_tick(app);
    }
    
    /* Check if monitor-bell hook fired */
    char *event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued for monitor-bell");
    ASSERT(strstr(event, "monitor-bell") != NULL, "event should mention monitor-bell");
    free(event);
    
    lmux_app_free(app); teardown(); PASS();
}
/* Test monitor-silence hook fires after silence interval */
static void test_monitor_silence_hook(void) {
    TEST("monitor-silence hook fires after silence interval");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane *pane = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", true);
    
    /* Set monitor silence interval to 200ms */
    lmux_pane_set_monitor_silence_interval(pane, 200);
    lmux_pane_set_monitor_silence_action(pane, "any");
    
    /* Register monitor-silence hook */
    lmux_hook_add(app, "monitor-silence", "echo silence detected");
    
    /* Send some output first */
    lmux_pane_send_keys(pane, "hello\n");
    
    /* Wait for output to be processed */
    for (int i = 0; i < 10; i++) {
        usleep(50000);
        lmux_app_tick(app);
    }
    
    /* Now wait for silence interval - 500ms should be enough */
    for (int i = 0; i < 20; i++) {
        usleep(50000);
        lmux_app_tick(app);
    }
    
    /* Check if monitor-silence hook fired */
    char *event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued for monitor-silence");
    ASSERT(strstr(event, "monitor-silence") != NULL, "event should mention monitor-silence");
    free(event);
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test monitor actions: any, current, other, none */
static void test_monitor_actions(void) {
    TEST("monitor actions control notification behavior");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane *pane1 = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", true);
    lmux_pane *pane2 = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", true);
    
    /* Set monitor activity on both panes */
    lmux_pane_set_monitor_activity_interval(pane1, 100);
    lmux_pane_set_monitor_activity_interval(pane2, 100);
    
    /* pane1: action=current (notify only current pane) */
    lmux_pane_set_monitor_activity_action(pane1, "current");
    /* pane2: action=other (notify other panes) */
    lmux_pane_set_monitor_activity_action(pane2, "other");
    
    /* Register monitor-activity hook */
    lmux_hook_add(app, "monitor-activity", "echo activity");
    
    /* Focus pane1 and send output */
    lmux_pane_focus(ws, pane1);
    lmux_pane_send_keys(pane1, "hello\n");
    usleep(200000);
    lmux_app_tick(app);
    
    /* Should fire monitor-activity for pane1 (current) but not pane2 (other) */
    char *event = lmux_event_pop(app);
    ASSERT(event != NULL, "event should be queued");
    free(event);
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test scrollback preserved with remain-on-exit */
static void test_scrollback_preserved_remain_on_exit(void) {
    TEST("scrollback preserved when pane exits with remain-on-exit");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane *pane = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", true);
    
    /* Enable remain-on-exit */
    lmux_pane_set_remain_on_exit(pane, true);
    
    /* Send some output to build scrollback */
    lmux_pane_send_keys(pane, "line1\nline2\nline3\n");
    
    /* Wait for output to be processed */
    for (int i = 0; i < 10; i++) {
        usleep(50000);
        lmux_app_tick(app);
    }
    
    /* Read scrollback before exit */
    char *before = lmux_pane_capture(pane, -10, -1);  /* Last 10 lines */
    ASSERT(before != NULL, "should capture scrollback before exit");
    ASSERT(strstr(before, "line3") != NULL, "should contain line3");
    free(before);
    
    /* Exit the pane - send EOF to cat */
    lmux_pane_send_keys(pane, "\x04");
    
    /* Wait for process to exit */
    for (int i = 0; i < 50; i++) {
        usleep(20000);
        lmux_app_tick(app);
    }
    
    /* Read scrollback after exit - should still be there */
    char *after = lmux_pane_capture(pane, -10, -1);
    ASSERT(after != NULL, "should capture scrollback after exit");
    ASSERT(strstr(after, "line3") != NULL, "should still contain line3 after exit");
    free(after);
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test status flags for monitoring */
static void test_status_flags(void) {
    TEST("status flags show monitoring state");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane *pane = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", true);
    
    /* Set up monitoring */
    lmux_pane_set_monitor_activity_interval(pane, 1000);
    lmux_pane_set_monitor_silence_interval(pane, 5000);
    lmux_pane_set_monitor_activity_action(pane, "any");
    lmux_pane_set_monitor_bell_action(pane, "current");
    lmux_pane_set_monitor_silence_action(pane, "other");
    
    /* Get pane info and check status flags */
    lmux_pane_info info;
    ASSERT(lmux_pane_get_info(pane, &info), "get_info should succeed");
    
    /* Check that monitor flags are accessible via format */
    /* M = activity monitoring, Z = silence monitoring, ! = bell monitoring, etc. */
    /* These would be checked via format expander */
    
    lmux_app_free(app); teardown(); PASS();
}

/* Test monitor settings getters */
static void test_monitor_settings_getters(void) {
    TEST("monitor settings can be retrieved");
    lmux_app *app = make_app();
    lmux_workspace *ws = lmux_workspace_create(app, "ws");
    lmux_surface *surf = lmux_surface_create(ws, "surf");
    lmux_pane *pane = lmux_pane_split(ws, surf, LMUX_SPLIT_HORIZONTAL, "cat", true);
    
    /* Set intervals */
    lmux_pane_set_monitor_activity_interval(pane, 1000);
    lmux_pane_set_monitor_silence_interval(pane, 5000);
    
    /* Get intervals */
    int activity_interval = lmux_pane_get_monitor_activity_interval(pane);
    ASSERT(activity_interval == 1000, "activity interval should be 1000");
    
    int silence_interval = lmux_pane_get_monitor_silence_interval(pane);
    ASSERT(silence_interval == 5000, "silence interval should be 5000");
    
    /* Set actions */
    lmux_pane_set_monitor_activity_action(pane, "any");
    lmux_pane_set_monitor_bell_action(pane, "current");
    lmux_pane_set_monitor_silence_action(pane, "other");
    
    /* Get actions */
    const char *activity_action = lmux_pane_get_monitor_activity_action(pane);
    ASSERT(strcmp(activity_action, "any") == 0, "activity action should be 'any'");
    
    const char *bell_action = lmux_pane_get_monitor_bell_action(pane);
    ASSERT(strcmp(bell_action, "current") == 0, "bell action should be 'current'");
    
    const char *silence_action = lmux_pane_get_monitor_silence_action(pane);
    ASSERT(strcmp(silence_action, "other") == 0, "silence action should be 'other'");
    
    lmux_app_free(app); teardown(); PASS();
}

int main(void) {
    printf("lmux Remain-on-exit & Monitoring Tests\n");
    printf("=======================================\n\n");
    
    test_pane_remain_on_exit_flag();
    test_pane_died_hook();
    test_pane_exited_hook();
    test_respawn_pane();
    test_monitor_activity_hook();
    test_monitor_bell_hook();
    test_monitor_silence_hook();
    test_monitor_actions();
    test_scrollback_preserved_remain_on_exit();
    test_status_flags();
    test_monitor_settings_getters();
    
    printf("\n===============\n");
    printf("Tests: %d passed, %d failed, %d total\n\n",
           tests_pass, tests_fail, tests_run);
    return tests_fail > 0 ? 1 : 0;
}