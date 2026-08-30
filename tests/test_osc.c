/**
 * test_osc.c — Unit tests for lmux OSC notification parser.
 *
 * The parser API evolved from returning lmux_osc_notification* to
 * using bool lmux_osc_parser_feed(..., const char **out_text, bool *out_waiting).
 * These tests match the current implementation.
 */
#include "lmux.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

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

/* ──────────────────────────────────────────────
 * Parser creation
 * ────────────────────────────────────────────── */

static void test_osc_parser_create(void) {
    TEST("osc_parser_new creates parser");
    lmux_osc_parser *p = lmux_osc_parser_new();
    ASSERT(p != NULL, "parser should be created");
    lmux_osc_parser_free(p);
    PASS();
}

/* ──────────────────────────────────────────────
 * OSC 9 parse
 * ────────────────────────────────────────────── */

static void test_osc_9_parse(void) {
    TEST("OSC 9 sequence parsed correctly");
    lmux_osc_parser *p = lmux_osc_parser_new();
    const char *text = NULL;
    bool waiting = false;

    /* OSC 9 notification: ESC ] 9 ; text ST */
    const char *seq = "\033]9;hello world\033\\";
    bool ok = lmux_osc_parser_feed(p, seq, strlen(seq), &text, &waiting);
    ASSERT(ok, "should parse OSC 9 sequence");
    ASSERT(text != NULL, "text should be non-NULL");
    ASSERT(strcmp(text, "hello world") == 0, "text should match");

    lmux_osc_parser_free(p);
    PASS();
}

/* ──────────────────────────────────────────────
 * OSC 777 parse
 * ────────────────────────────────────────────── */

static void test_osc_777_parse(void) {
    TEST("OSC 777 sequence parsed correctly");
    lmux_osc_parser *p = lmux_osc_parser_new();
    const char *text = NULL;
    bool waiting = false;

    /* OSC 777: ESC ] 777 ; key=value ; key=value ST */
    const char *seq = "\033]777;type=test;text=notification message\033\\";
    bool ok = lmux_osc_parser_feed(p, seq, strlen(seq), &text, &waiting);
    ASSERT(ok, "should parse OSC 777 sequence");
    ASSERT(text != NULL, "text should be non-NULL");

    lmux_osc_parser_free(p);
    PASS();
}

/* ──────────────────────────────────────────────
 * OSC 99 parse
 * ────────────────────────────────────────────── */

static void test_osc_99_parse(void) {
    TEST("OSC 99 sequence parsed correctly");
    lmux_osc_parser *p = lmux_osc_parser_new();
    const char *text = NULL;
    bool waiting = false;

    /* OSC 99: ESC ] 99 ; title ST */
    const char *seq = "\033]99;My Terminal Title\033\\";
    bool ok = lmux_osc_parser_feed(p, seq, strlen(seq), &text, &waiting);
    ASSERT(ok, "should parse OSC 99 sequence");
    ASSERT(text != NULL, "text should be non-NULL");
    ASSERT(strcmp(text, "My Terminal Title") == 0, "text should match");

    lmux_osc_parser_free(p);
    PASS();
}

/* ──────────────────────────────────────────────
 * Partial feed
 * ────────────────────────────────────────────── */

static void test_osc_partial_feed(void) {
    TEST("partial OSC sequence does not produce notification");
    lmux_osc_parser *p = lmux_osc_parser_new();
    const char *text = NULL;
    bool waiting = false;

    const char *partial = "\033]9;incomplete";
    bool ok = lmux_osc_parser_feed(p, partial, strlen(partial), &text, &waiting);
    ASSERT(!ok, "partial sequence should not complete");

    lmux_osc_parser_free(p);
    PASS();
}

/* ──────────────────────────────────────────────
 * Multi-feed
 * ────────────────────────────────────────────── */

static void test_osc_multi_feed(void) {
    TEST("split OSC feed across multiple calls produces notification");
    lmux_osc_parser *p = lmux_osc_parser_new();
    const char *text = NULL;
    bool waiting = false;

    const char *part1 = "\033]9;hello ";
    const char *part2 = "world\033\\";

    bool ok1 = lmux_osc_parser_feed(p, part1, strlen(part1), &text, &waiting);
    ASSERT(!ok1, "first part should not complete");

    bool ok2 = lmux_osc_parser_feed(p, part2, strlen(part2), &text, &waiting);
    ASSERT(ok2, "second part should complete sequence");
    ASSERT(text != NULL, "text should be non-NULL");
    ASSERT(strcmp(text, "hello world") == 0, "full text should match");

    lmux_osc_parser_free(p);
    PASS();
}

/* ──────────────────────────────────────────────
 * BEL terminator
 * ────────────────────────────────────────────── */

static void test_osc_bel_terminator(void) {
    TEST("OSC sequence with BEL terminator (\\a) parsed correctly");
    lmux_osc_parser *p = lmux_osc_parser_new();
    const char *text = NULL;
    bool waiting = false;

    const char *seq = "\033]9;bell terminated\007";
    bool ok = lmux_osc_parser_feed(p, seq, strlen(seq), &text, &waiting);
    ASSERT(ok, "BEL-terminated sequence should parse");
    ASSERT(text != NULL, "text should be non-NULL");
    ASSERT(strcmp(text, "bell terminated") == 0, "text should match");

    lmux_osc_parser_free(p);
    PASS();
}

/* ──────────────────────────────────────────────
 * Reset (create new parser to simulate)
 * ────────────────────────────────────────────── */

static void test_osc_reset(void) {
    TEST("new parser after partial feed works correctly");
    lmux_osc_parser *p = lmux_osc_parser_new();
    const char *text = NULL;
    bool waiting = false;

    /* Feed a partial sequence — let it accumulate */
    const char *partial = "\033]9;";
    lmux_osc_parser_feed(p, partial, strlen(partial), &text, &waiting);

    /* Create a fresh parser (simulates reset) */
    lmux_osc_parser_free(p);
    p = lmux_osc_parser_new();

    /* Feed a complete sequence and verify it still works */
    const char *seq = "\033]9;after reset\033\\";
    bool ok = lmux_osc_parser_feed(p, seq, strlen(seq), &text, &waiting);
    ASSERT(ok, "should still parse with fresh parser");
    ASSERT(text != NULL, "text should be non-NULL");
    ASSERT(strcmp(text, "after reset") == 0, "text should match");

    lmux_osc_parser_free(p);
    PASS();
}

/* ──────────────────────────────────────────────
 * Main
 * ────────────────────────────────────────────── */

int main(void) {
    printf("lmux OSC Parser Unit Tests\n");
    printf("==========================\n\n");

    test_osc_parser_create();
    test_osc_9_parse();
    test_osc_777_parse();
    test_osc_99_parse();
    test_osc_partial_feed();
    test_osc_multi_feed();
    test_osc_bel_terminator();
    test_osc_reset();

    printf("\n==========================\n");
    printf("Tests: %d passed, %d failed, %d total\n\n",
           tests_pass, tests_fail, tests_run);

    return tests_fail > 0 ? 1 : 0;
}
