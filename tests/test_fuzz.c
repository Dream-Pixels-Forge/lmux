/**
 * test_fuzz.c — Fuzz harness for the lmux OSC notification parser.
 *
 * Exercises the parser with:
 *   1. Random byte sequences (fixed seed for reproducibility)
 *   2. Known malicious / adversarial patterns
 *   3. Boundary conditions (overflow attempts, unterminated input)
 *
 * The parser must never crash, invoke undefined behavior, or leak
 * memory (the latter is verified by running under valgrind / ASan).
 *
 * Compile:
 *   gcc -std=c17 -Wall -Wextra -I include \
 *       -o build/test_fuzz tests/test_fuzz.c build/liblmux_core.a
 */
#define _POSIX_C_SOURCE 200809L
#include "lmux.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <setjmp.h>
#include <stdint.h>

/* ── Reproducible PRNG ─────────────────────────────────────────── */
#define FUZZ_SEED 0xDEADBEEF
#define FUZZ_ITERATIONS 10000
#define FUZZ_MAX_LEN    1024

static uint32_t xorshift32(uint32_t *state) {
    uint32_t x = *state;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    *state = x;
    return x;
}

static int rng_next(uint32_t *state, int lo, int hi) {
    return lo + (int)(xorshift32(state) % (uint32_t)(hi - lo + 1));
}

/* ── Crash / signal detection ──────────────────────────────────── */
static volatile sig_atomic_t caught_signal = 0;
static jmp_buf jump_buf;

static void signal_handler(int sig) {
    caught_signal = sig;
    longjmp(jump_buf, 1);
}

static int install_signal_handlers(void) {
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = signal_handler;
    sa.sa_flags   = SA_RESTART;
    sigemptyset(&sa.sa_mask);

    int ok = 1;
    if (sigaction(SIGSEGV, &sa, NULL) != 0) ok = 0;
    if (sigaction(SIGABRT, &sa, NULL) != 0) ok = 0;
    if (sigaction(SIGFPE,  &sa, NULL) != 0) ok = 0;
    if (sigaction(SIGBUS,  &sa, NULL) != 0) ok = 0;
#ifdef SIGSTKFLT
    if (sigaction(SIGSTKFLT, &sa, NULL) != 0) ok = 0;
#endif
    return ok;
}

static void reset_signal_handlers(void) {
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = SIG_DFL;
    sigaction(SIGSEGV, &sa, NULL);
    sigaction(SIGABRT, &sa, NULL);
    sigaction(SIGFPE,  &sa, NULL);
    sigaction(SIGBUS,  &sa, NULL);
#ifdef SIGSTKFLT
    sigaction(SIGSTKFLT, &sa, NULL);
#endif
}

/* ── Helpers ───────────────────────────────────────────────────── */
static size_t total_feeds    = 0;
static size_t total_parsed   = 0;
static size_t total_errors   = 0;

/**
 * feed_once — Feed `n` bytes to a fresh parser.
 * Verifies: no crash (longjmp), no assertion failure.
 * Returns 0 on ok, 1 on crash/signal.
 */
static int feed_once(const char *data, size_t n) {
    total_feeds++;

    if (setjmp(jump_buf) != 0) {
        /* Caught a signal — parser crashed. */
        total_errors++;
        fprintf(stderr, "  CRASH: signal %d on %zu-byte input\n",
                caught_signal, n);
        reset_signal_handlers();
        return 1;
    }

    caught_signal = 0;

    lmux_osc_parser *p = lmux_osc_parser_new();
    if (!p) {
        fprintf(stderr, "  ERROR: lmux_osc_parser_new() returned NULL\n");
        total_errors++;
        return 1;
    }

    const char *out_text = NULL;
    bool out_waiting = false;

    /* Feed the entire buffer at once. */
    bool parsed = lmux_osc_parser_feed(p, data, n, &out_text, &out_waiting);
    if (parsed) {
        total_parsed++;
        /* out_text should be valid if parsed is true. */
        if (!out_text) {
            fprintf(stderr, "  ERROR: feed returned true but out_text is NULL\n");
            total_errors++;
            lmux_osc_parser_free(p);
            return 1;
        }
    }

    /* Also try feeding byte-by-byte to stress the state machine. */
    lmux_osc_parser_free(p);

    p = lmux_osc_parser_new();
    if (!p) {
        fprintf(stderr, "  ERROR: second lmux_osc_parser_new() returned NULL\n");
        total_errors++;
        return 1;
    }

    for (size_t i = 0; i < n; i++) {
        out_text = NULL;
        out_waiting = false;
        parsed = lmux_osc_parser_feed(p, &data[i], 1, &out_text, &out_waiting);
        if (parsed && !out_text) {
            fprintf(stderr, "  ERROR: byte-wise feed returned true but out_text NULL at offset %zu\n", i);
            total_errors++;
            lmux_osc_parser_free(p);
            return 1;
        }
    }

    /* Note: we do NOT re-feed the entire buffer on the same parser
     * instance after byte-by-byte feeding. The parser's internal state
     * may have consumed some bytes, and re-feeding the original buffer
     * could cause reads past the end of the input. */
    lmux_osc_parser_free(p);
    return 0;
}

/* ── Phase 1: Random fuzzing ──────────────────────────────────── */
static int fuzz_random(uint32_t seed, int iterations) {
    printf("\n=== Phase 1: Random fuzzing (%d iterations, seed 0x%X) ===\n",
           iterations, seed);
    uint32_t rng = seed;
    int failures = 0;

    for (int i = 0; i < iterations; i++) {
        /* Vary length: mostly small, occasionally large. */
        int len;
        int bucket = rng_next(&rng, 0, 99);
        if (bucket < 60)      len = rng_next(&rng, 1, 32);      /* 60% tiny */
        else if (bucket < 85) len = rng_next(&rng, 33, 256);    /* 25% small */
        else if (bucket < 95) len = rng_next(&rng, 257, 1024);  /* 10% medium */
        else                  len = rng_next(&rng, 1, 4);        /*  5% 1-byte */

        char buf[FUZZ_MAX_LEN];
        for (int j = 0; j < len; j++) {
            buf[j] = (char)rng_next(&rng, 0, 255);
        }

        if (feed_once(buf, (size_t)len)) {
            failures++;
            fprintf(stderr, "  FAIL at iteration %d (len=%d)\n", i, len);
        }

        if ((i + 1) % 2000 == 0) {
            printf("  ... %d / %d iterations done\n", i + 1, iterations);
        }
    }

    printf("  Random fuzzing: %d failures\n", failures);
    return failures;
}

/* ── Phase 2: Known malicious / adversarial patterns ───────────── */

/** Helper: feed a string literal safely. */
static int feed_str(const char *s) {
    return feed_once(s, strlen(s));
}

/** Helper: feed raw bytes (may contain NUL). */
static int feed_raw(const char *data, size_t n) {
    return feed_once(data, n);
}

static int fuzz_malicious_patterns(void) {
    printf("\n=== Phase 2: Malicious / adversarial patterns ===\n");
    int failures = 0;

    /* ── 2a: Valid OSC sequences that parse successfully ────── */
    printf("  2a: Known-good OSC sequences...\n");
    failures += feed_str("\033]9;hello world\007");            /* OSC 9, BEL */
    failures += feed_str("\033]9;hello world\033\\");           /* OSC 9, ST */
    failures += feed_str("\033]99;i;task done\007");            /* OSC 99, BEL */
    failures += feed_str("\033]99;task done\007");              /* OSC 99, no meta */
    failures += feed_str("\033]777;notify;alert!\007");         /* OSC 777, BEL */
    failures += feed_str("\033]777;notify;alert!\033\\");       /* OSC 777, ST */
    failures += feed_str("\033]9; \007");                       /* whitespace body */
    failures += feed_str("\033]99;i; \007");                    /* waiting flag */
    failures += feed_str("\033]9;\007");                        /* empty body */
    failures += feed_str("\033]777;notify;\007");               /* empty 777 body */

    /* ── 2b: Null bytes in the middle ───────────────────────── */
    printf("  2b: Null bytes embedded in input...\n");
    {
        const char d1[] = "hel\0lo world\007";
        failures += feed_raw(d1, sizeof(d1) - 1);
    }
    {
        const char d2[] = "\033]9;te\0st\007";
        failures += feed_raw(d2, sizeof(d2) - 1);
    }
    {
        const char d3[] = "\033\0]9;bad\007";
        failures += feed_raw(d3, sizeof(d3) - 1);
    }
    {
        const char d4[] = "\033]9;AAAA\0BBBB\0CCCC\007";
        failures += feed_raw(d4, sizeof(d4) - 1);
    }
    /* NUL as very first byte */
    {
        const char d5[] = "\0\0\0\033]9;ok\007";
        failures += feed_raw(d5, sizeof(d5) - 1);
    }
    /* Entire buffer is NUL bytes */
    {
        char all_null[64];
        memset(all_null, 0, sizeof(all_null));
        failures += feed_raw(all_null, sizeof(all_null));
    }

    /* ── 2c: Very long sequences (>64KB) ────────────────────── */
    printf("  2c: Very long sequences (64KB+)...\n");
    {
        /* OSC 9 with 65536 bytes of 'A' + BEL */
        size_t big_len = 65536 + 6; /* \033]9; + As + \007 */
        char *big = malloc(big_len);
        if (big) {
            big[0] = '\033';
            big[1] = ']';
            big[2] = '9';
            big[3] = ';';
            memset(big + 4, 'A', 65536);
            big[big_len - 1] = '\007';
            failures += feed_raw(big, big_len);
            free(big);
        }
    }
    {
        /* OSC 9 with 128KB of 'B' + ST */
        size_t big2_len = 131072 + 5; /* \033]9; + Bs + \033\\ */
        char *big2 = malloc(big2_len);
        if (big2) {
            big2[0] = '\033';
            big2[1] = ']';
            big2[2] = '9';
            big2[3] = ';';
            memset(big2 + 4, 'B', 131072);
            big2[big2_len - 2] = '\033';
            big2[big2_len - 1] = '\\';
            failures += feed_raw(big2, big2_len);
            free(big2);
        }
    }
    {
        /* Pure garbage, 256KB */
        size_t huge_len = 262144;
        char *huge = malloc(huge_len);
        if (huge) {
            for (size_t i = 0; i < huge_len; i++) {
                huge[i] = (char)(i & 0xFF);
            }
            failures += feed_raw(huge, huge_len);
            free(huge);
        }
    }
    {
        /* Very long incomplete sequence (no terminator) */
        size_t no_term_len = 70000;
        char *no_term = malloc(no_term_len);
        if (no_term) {
            no_term[0] = '\033';
            no_term[1] = ']';
            no_term[2] = '9';
            no_term[3] = ';';
            memset(no_term + 4, 'X', no_term_len - 4);
            failures += feed_raw(no_term, no_term_len);
            free(no_term);
        }
    }

    /* ── 2d: Incomplete sequences (no BEL/ST terminator) ────── */
    printf("  2d: Unterminated sequences...\n");
    failures += feed_str("\033]9;partial");
    failures += feed_str("\033]99;i;waiting");
    failures += feed_str("\033]777;notify;text");
    failures += feed_str("\033]9;");
    failures += feed_str("\033]99;i;");
    failures += feed_str("\033]777;notify;");
    failures += feed_str("\033]9");
    failures += feed_str("\033]99");
    failures += feed_str("\033]777");
    failures += feed_str("\033]");
    failures += feed_str("\033");
    failures += feed_str("");   /* empty input */
    failures += feed_str("\007"); /* standalone BEL */
    failures += feed_str("\033\\"); /* standalone ST */

    /* ── 2e: UTF-8 invalid sequences ────────────────────────── */
    printf("  2e: Invalid / malformed UTF-8...\n");
    {
        /* Overlong 2-byte encoding of U+0000 */
        const char d[] = "\033]9;\xC0\x80\007";
        failures += feed_raw(d, sizeof(d) - 1);
    }
    {
        /* Overlong 3-byte encoding of U+002F */
        const char d[] = "\033]9;\xE0\x80\xAF\007";
        failures += feed_raw(d, sizeof(d) - 1);
    }
    {
        /* Overlong 4-byte encoding of U+007F */
        const char d[] = "\033]9;\xF0\x80\x80\xBF\007";
        failures += feed_raw(d, sizeof(d) - 1);
    }
    {
        /* Truncated 2-byte sequence */
        const char d[] = "\033]9;\xC2\007";
        failures += feed_raw(d, sizeof(d) - 1);
    }
    {
        /* Truncated 3-byte sequence */
        const char d[] = "\033]9;\xE2\x82\007";
        failures += feed_raw(d, sizeof(d) - 1);
    }
    {
        /* Truncated 4-byte sequence */
        const char d[] = "\033]9;\xF0\x90\x8D\007";
        failures += feed_raw(d, sizeof(d) - 1);
    }
    {
        /* 0xFE and 0xFF bytes (invalid in any UTF-8) */
        const char d[] = "\033]9;\xFE\xFF\007";
        failures += feed_raw(d, sizeof(d) - 1);
    }
    {
        /* Continuation byte without starter */
        const char d[] = "\033]9;\x80\x80\x80\007";
        failures += feed_raw(d, sizeof(d) - 1);
    }
    {
        /* Surrogate halves (U+D800) */
        const char d[] = "\033]9;\xED\xA0\x80\007";
        failures += feed_raw(d, sizeof(d) - 1);
    }
    {
        /* 5-byte and 6-byte sequences (beyond valid UTF-8) */
        const char d[] = "\033]9;\xF8\x80\x80\x80\x80\007";
        failures += feed_raw(d, sizeof(d) - 1);
    }
    {
        const char d[] = "\033]9;\xFC\x80\x80\x80\x80\x80\007";
        failures += feed_raw(d, sizeof(d) - 1);
    }

    /* ── 2f: Embedded quotes, backslashes, special chars ────── */
    printf("  2f: Special characters in OSC body...\n");
    failures += feed_str("\033]9;\"quoted\"\007");
    failures += feed_str("\033]9;back\\slash\007");
    failures += feed_str("\033]9;newline\nin\007");
    failures += feed_str("\033]9;tab\there\007");
    failures += feed_str("\033]9;carriage\rreturn\007");
    failures += feed_str("\033]9;${SHELL}\007");
    failures += feed_str("\033]9;`whoami`\007");
    failures += feed_str("\033]9;$(cmd)\007");
    failures += feed_str("\033]9;{{template}}\007");
    failures += feed_str("\033]9;%0a%0d%00\007");
    failures += feed_str("\033]9;../../etc/passwd\007");
    failures += feed_str("\033]9;\x1B[31mred\x1B[0m\007"); /* ANSI in body */
    failures += feed_str("\033]9;\"'; DROP TABLE--\007");

    /* OSC 99 with meta containing special chars */
    failures += feed_str("\033]99;\"meta\";text\007");
    failures += feed_str("\033]99;i;text\\with\\escapes\007");
    failures += feed_str("\033]99;;empty meta separator\007");

    /* OSC 777 with unusual formats */
    failures += feed_str("\033]777;notify;\007");
    failures += feed_str("\033]777;;text\007");

    /* ── 2g: Boundary / overflow attempts ───────────────────── */
    printf("  2g: Buffer boundary / overflow attempts...\n");
    {
        /* Exactly 2048 bytes of body (buf is 2048) */
        size_t boundary = 2048;
        char *buf = malloc(boundary + 10);
        if (buf) {
            buf[0] = '\033';
            buf[1] = ']';
            buf[2] = '9';
            buf[3] = ';';
            memset(buf + 4, 'Z', boundary - 4);
            buf[boundary - 1] = '\007'; /* BEL at position 2047 */
            failures += feed_raw(buf, boundary);
            free(buf);
        }
    }
    {
        /* 4096 bytes body (overflow past buf[2048]) */
        size_t over = 4096;
        char *buf = malloc(over + 10);
        if (buf) {
            buf[0] = '\033';
            buf[1] = ']';
            buf[2] = '9';
            buf[3] = ';';
            memset(buf + 4, 'Y', over - 4);
            buf[over - 1] = '\007';
            failures += feed_raw(buf, over);
            free(buf);
        }
    }
    {
        /* OSC ps = very long number (>int range) */
        failures += feed_str("\033]999999999999999999;body\007");
    }
    {
        /* OSC ps with non-numeric garbage */
        failures += feed_str("\033]XYZ;body\007");
        failures += feed_str("\033]-1;body\007");
        failures += feed_str("\033]999;body\007");
        failures += feed_str("\033]0;body\007");
    }

    /* ── 2h: Multiple sequential OSC sequences ──────────────── */
    printf("  2h: Multiple back-to-back sequences...\n");
    {
        /* Many short sequences concatenated */
        const char seqs[] =
            "\033]9;first\007"
            "\033]9;second\007"
            "\033]99;i;third\007"
            "\033]777;notify;fourth\007"
            "\033]9;fifth\033\\"
            "\033]9;sixth\007";
        failures += feed_str(seqs);
    }
    {
        /* Sequence immediately followed by garbage */
        failures += feed_str("\033]9;valid\007GARBAGE\033]9;also-valid\007");
    }
    {
        /* Interleaved incomplete + complete */
        failures += feed_str(
            "\033]9;complete\007"
            "\033]99;i;partial"
            "\033]9;another\007");
    }

    /* ── 2i: Split sequences (feed in two parts) ────────────── */
    printf("  2i: Split / fragmented sequences...\n");
    {
        /* Split in the middle of the ps number */
        lmux_osc_parser *p = lmux_osc_parser_new();
        const char *out = NULL;
        bool waiting = false;

        lmux_osc_parser_feed(p, "\033]9", 2, &out, &waiting);
        bool got = lmux_osc_parser_feed(p, "9;rest\007", 8, &out, &waiting);
        if (got && !out) { failures++; total_errors++; }
        lmux_osc_parser_free(p);
        total_feeds += 2;
    }
    {
        /* Split ESC\ across two feeds */
        lmux_osc_parser *p = lmux_osc_parser_new();
        const char *out = NULL;
        bool waiting = false;

        lmux_osc_parser_feed(p, "\033]9;hello\033", 12, &out, &waiting);
        bool got = lmux_osc_parser_feed(p, "\\", 1, &out, &waiting);
        if (got && !out) { failures++; total_errors++; }
        lmux_osc_parser_free(p);
        total_feeds += 2;
    }
    {
        /* Split BEL from body */
        lmux_osc_parser *p = lmux_osc_parser_new();
        const char *out = NULL;
        bool waiting = false;

        lmux_osc_parser_feed(p, "\033]9;text", 8, &out, &waiting);
        bool got = lmux_osc_parser_feed(p, "\007", 1, &out, &waiting);
        if (got && !out) { failures++; total_errors++; }
        lmux_osc_parser_free(p);
        total_feeds += 2;
    }

    /* ── 2j: Fuzzed meta field for OSC 99 ───────────────────── */
    printf("  2j: OSC 99 meta field edge cases...\n");
    failures += feed_str("\033]99;i\007");                     /* no semicolon */
    failures += feed_str("\033]99;iiiiiiiii;body\007");        /* many i's */
    failures += feed_str("\033]99;\007");                      /* empty meta */
    failures += feed_str("\033]99;;;body\007");                /* double semicolon */
    {
        /* Meta field exactly 256 bytes (meta[256] boundary) */
        size_t meta_len = 256;
        char *buf = malloc(meta_len + 20);
        if (buf) {
            buf[0] = '\033';
            buf[1] = ']';
            buf[2] = '9';
            buf[3] = '9';
            buf[4] = ';';
            memset(buf + 5, 'x', meta_len);
            buf[5 + meta_len] = ';';
            buf[6 + meta_len] = 't';
            buf[7 + meta_len] = 'e';
            buf[8 + meta_len] = 'x';
            buf[9 + meta_len] = 't';
            buf[10 + meta_len] = '\007';
            failures += feed_raw(buf, meta_len + 11);
            free(buf);
        }
    }

    printf("  Malicious patterns: %d failures\n", failures);
    return failures;
}

/* ── Phase 3: Randomized adversarial with fixed seed ───────────── */
static int fuzz_adversarial_random(uint32_t seed, int iterations) {
    printf("\n=== Phase 3: Adversarial random (%d iterations, seed 0x%X) ===\n",
           iterations, seed);
    uint32_t rng = seed;
    int failures = 0;

    /* Pre-build some "poison" bytes that are especially interesting
     * to the OSC parser state machine. */
    static const unsigned char poison[] = {
        0x00, 0x07, 0x1B, '\\', ';', '[', ']',
        0x80, 0xBF, 0xC0, 0xFE, 0xFF,
        '9', '9', '9', '7', '7', '7',
    };
    #define NPOISON (sizeof(poison) / sizeof(poison[0]))

    for (int i = 0; i < iterations; i++) {
        int len = rng_next(&rng, 1, 512);
        char buf[512];

        for (int j = 0; j < len; j++) {
            int pick = rng_next(&rng, 0, 99);
            if (pick < 40) {
                /* 40% poison bytes */
                buf[j] = (char)poison[rng_next(&rng, 0, NPOISON - 1)];
            } else if (pick < 60) {
                /* 20% digits (feeds into OSC ps accumulator) */
                buf[j] = (char)rng_next(&rng, '0', '9');
            } else {
                /* 40% random */
                buf[j] = (char)rng_next(&rng, 0, 255);
            }
        }

        if (feed_once(buf, (size_t)len)) {
            failures++;
        }

        if ((i + 1) % 2000 == 0) {
            printf("  ... %d / %d iterations done\n", i + 1, iterations);
        }
    }

    printf("  Adversarial random: %d failures\n", failures);
    return failures;
}

/* ── Main ──────────────────────────────────────────────────────── */
int main(void) {
    printf("=== OSC Parser Fuzz Test ===\n");
    printf("OSC parser fuzz test (compiled %s %s)\n\n", __DATE__, __TIME__);

    if (!install_signal_handlers()) {
        fprintf(stderr, "WARNING: could not install signal handlers\n");
    }

    int total_failures = 0;

    total_failures += fuzz_random(FUZZ_SEED, FUZZ_ITERATIONS);
    total_failures += fuzz_malicious_patterns();
    total_failures += fuzz_adversarial_random(FUZZ_SEED ^ 0xCAFEBABE, FUZZ_ITERATIONS);

    reset_signal_handlers();

    printf("\n=== Summary ===\n");
    printf("  Total feeds:    %zu\n", total_feeds);
    printf("  Total parsed:   %zu\n", total_parsed);
    printf("  Total errors:   %zu\n", total_errors);
    printf("  Failures:       %d\n", total_failures);

    if (total_failures > 0 || total_errors > 0) {
        printf("\nRESULT: FAIL\n");
        return 1;
    }

    printf("\nRESULT: PASS\n");
    return 0;
}
