/**
 * test_json_fuzz.c — Regression + fuzz tests for the JSON request layer.
 *
 * Covers src/core/json.c (the field extractors every command handler uses)
 * and the dispatcher on top of it:
 *
 *   1. Regressions: a \uXXXX escape at the end of a full buffer used to
 *      write past it (stack overflow from a single request); a trailing
 *      backslash read past the terminator; text inside a value could be
 *      matched as a key; surrogate pairs decoded to U+FFFD.
 *   2. Random fuzzing of the extractors with guard bytes around the output
 *      buffer, so overruns fail even without ASan.
 *   3. Dispatcher fuzzing: random strings (quotes, backslashes, controls,
 *      UTF-8) are sent as titles/names/text, and every response AND every
 *      event-stream entry must still be valid JSON.
 *
 * Run under ASan with: node scripts/test.mjs --sanitize --unit
 */
#define _POSIX_C_SOURCE 200809L
#include "lmux.h"
#include "lmux_json.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int tests_run = 0, tests_pass = 0, tests_fail = 0;

#define TEST(name) do { tests_run++; printf("  TEST %s ... ", name); fflush(stdout); } while (0)
#define PASS() do { tests_pass++; printf("PASS\n"); } while (0)
#define FAIL(...) do { tests_fail++; printf("FAIL: "); printf(__VA_ARGS__); printf("\n"); } while (0)
#define CHECK(cond, ...) do { if (!(cond)) { FAIL(__VA_ARGS__); return; } } while (0)

/* ── Reproducible PRNG ─────────────────────────────────────────── */
static uint32_t rng_state = 0xC0FFEEu;
static uint32_t rnd(void) {
    uint32_t x = rng_state;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    return rng_state = x;
}

/* ── Minimal strict JSON validator ─────────────────────────────── */
typedef struct { const char *p; int depth; } jv;
static int jv_value(jv *v);
static void jv_ws(jv *v) { while (*v->p == ' ' || *v->p == '\t' || *v->p == '\n' || *v->p == '\r') v->p++; }
static int jv_string(jv *v) {
    if (*v->p != '"') return 0;
    v->p++;
    while (*v->p != '"') {
        unsigned char c = (unsigned char)*v->p;
        if (c == 0 || c < 0x20) return 0;
        if (c == '\\') {
            v->p++;
            if (*v->p == 'u') {
                for (int i = 1; i <= 4; i++) {
                    char h = v->p[i];
                    if (!((h >= '0' && h <= '9') || (h >= 'a' && h <= 'f') || (h >= 'A' && h <= 'F'))) return 0;
                }
                v->p += 5;
                continue;
            }
            if (!strchr("\"\\/bfnrt", *v->p) || !*v->p) return 0;
        }
        v->p++;
    }
    v->p++;
    return 1;
}
static int jv_number(jv *v) {
    const char *s = v->p;
    if (*v->p == '-') v->p++;
    while ((*v->p >= '0' && *v->p <= '9') || *v->p == '.' || *v->p == 'e' || *v->p == 'E' || *v->p == '+' || *v->p == '-') v->p++;
    return v->p > s;
}
static int jv_value(jv *v) {
    if (++v->depth > 64) return 0;
    jv_ws(v);
    int ok = 0;
    if (*v->p == '{') {
        v->p++; jv_ws(v);
        if (*v->p == '}') { v->p++; ok = 1; }
        else for (;;) {
            jv_ws(v);
            if (!jv_string(v)) break;
            jv_ws(v);
            if (*v->p != ':') break;
            v->p++;
            if (!jv_value(v)) break;
            jv_ws(v);
            if (*v->p == ',') { v->p++; continue; }
            if (*v->p == '}') { v->p++; ok = 1; }
            break;
        }
    } else if (*v->p == '[') {
        v->p++; jv_ws(v);
        if (*v->p == ']') { v->p++; ok = 1; }
        else for (;;) {
            if (!jv_value(v)) break;
            jv_ws(v);
            if (*v->p == ',') { v->p++; continue; }
            if (*v->p == ']') { v->p++; ok = 1; }
            break;
        }
    } else if (*v->p == '"') ok = jv_string(v);
    else if (!strncmp(v->p, "true", 4)) { v->p += 4; ok = 1; }
    else if (!strncmp(v->p, "false", 5)) { v->p += 5; ok = 1; }
    else if (!strncmp(v->p, "null", 4)) { v->p += 4; ok = 1; }
    else ok = jv_number(v);
    v->depth--;
    return ok;
}
static int json_valid(const char *s) {
    if (!s) return 0;
    jv v = { s, 0 };
    if (!jv_value(&v)) return 0;
    jv_ws(&v);
    return *v.p == 0;
}

/* ── Guarded output buffer ─────────────────────────────────────── */
#define GUARD 32
static unsigned char guarded[GUARD + 4096 + GUARD];
static char *guard_buf(size_t cap) {
    (void)cap;
    memset(guarded, 0xA5, sizeof guarded);
    return (char *)guarded + GUARD;
}
static int guard_ok(size_t cap) {
    for (size_t i = 0; i < GUARD; i++)
        if (guarded[i] != 0xA5 || guarded[GUARD + cap + i] != 0xA5) return 0;
    return 1;
}

/* ── 1. Regressions ────────────────────────────────────────────── */

static void test_unicode_escape_at_buffer_end(void) {
    TEST("\\u escape at the end of a full buffer stays in bounds");
    for (size_t cap = 1; cap <= 16; cap++) {
        for (size_t fill = 0; fill <= cap + 2; fill++) {
            char req[256];
            int n = snprintf(req, sizeof req, "{\"title\":\"");
            for (size_t i = 0; i < fill; i++) req[n++] = 'a';
            n += snprintf(req + n, sizeof req - n, "\\u20ac\\ud83d\\ude00\"}");
            char *out = guard_buf(cap);
            CHECK(lmux_json_extract_string(req, "title", out, cap), "extract failed cap=%zu", cap);
            CHECK(guard_ok(cap), "wrote outside buffer (cap=%zu fill=%zu)", cap, fill);
            CHECK(strlen(out) < cap, "unterminated (cap=%zu fill=%zu)", cap, fill);
        }
    }
    /* The exact shape of the original crash: 254 chars + \u20ac into 256. */
    char req[512];
    int n = snprintf(req, sizeof req, "{\"title\":\"");
    for (int i = 0; i < 254; i++) req[n++] = 'a';
    snprintf(req + n, sizeof req - n, "\\u20ac\"}");
    char *out = guard_buf(256);
    lmux_json_extract_string(req, "title", out, 256);
    CHECK(guard_ok(256), "original crash input overflowed");
    CHECK(strlen(out) == 254, "multi-byte char must be dropped whole, got len %zu", strlen(out));
    PASS();
}

static void test_trailing_backslash(void) {
    TEST("trailing backslash does not read past the terminator");
    char *heap = malloc(16);
    memcpy(heap, "{\"t\":\"ab\\", 10);   /* exact-size allocation: ASan catches over-read */
    char out[16];
    CHECK(lmux_json_extract_string(heap, "t", out, sizeof out), "extract failed");
    CHECK(strcmp(out, "ab") == 0, "got '%s'", out);
    free(heap);
    PASS();
}

static void test_surrogates(void) {
    TEST("surrogate pairs decode to 4-byte UTF-8; lone ones to U+FFFD");
    char out[32];
    lmux_json_extract_string("{\"t\":\"\\ud83d\\ude00\"}", "t", out, sizeof out);
    CHECK(strcmp(out, "\xF0\x9F\x98\x80") == 0, "emoji not decoded");
    lmux_json_extract_string("{\"t\":\"x\\ud83dy\"}", "t", out, sizeof out);
    CHECK(strcmp(out, "x\xEF\xBF\xBDy") == 0, "lone high surrogate");
    lmux_json_extract_string("{\"t\":\"\\ude00\"}", "t", out, sizeof out);
    CHECK(strcmp(out, "\xEF\xBF\xBD") == 0, "lone low surrogate");
    lmux_json_extract_string("{\"t\":\"a\\u00zzb\"}", "t", out, sizeof out);
    CHECK(strncmp(out, "a\xEF\xBF\xBD", 4) == 0, "bad hex -> U+FFFD");
    lmux_json_extract_string("{\"t\":\"a\\u0000b\"}", "t", out, sizeof out);
    CHECK(strcmp(out, "ab") == 0, "\\u0000 dropped");
    PASS();
}

static void test_key_inside_value_not_matched(void) {
    TEST("text inside a value is never matched as a key");
    const char *req = "{\"text\":\"echo \\\"pane\\\":\\\"3\\\"\",\"pane\":\"7\"}";
    char out[16];
    CHECK(lmux_json_extract_string(req, "pane", out, sizeof out), "pane missing");
    CHECK(strcmp(out, "7") == 0, "got pane '%s' (matched inside text)", out);
    CHECK(!lmux_json_find_key("{\"text\":\"\\\"id\\\":5\"}", "id"), "matched key inside value");
    CHECK(!lmux_json_find_key("{\"a\":\"id\"}", "id"), "matched a value string as key");
    PASS();
}

static void test_toplevel(void) {
    TEST("top-level lookup ignores nested keys");
    const char *req = "{\"args\":{\"cmd\":\"evil\"},\"cmd\":\"workspace.list\"}";
    const char *v = lmux_json_find_toplevel_key(req, "cmd", NULL);
    char out[32];
    CHECK(v && lmux_json_decode_string(v, out, sizeof out), "no top-level cmd");
    CHECK(strcmp(out, "workspace.list") == 0, "got '%s'", out);
    PASS();
}

static void test_escape(void) {
    TEST("escape round-trips and never splits a sequence when truncating");
    const char *in = "q\"b\\n\n\x01\x7f \xE2\x82\xAC";
    char esc[128], back[128], wrapped[160];
    lmux_json_escape(esc, sizeof esc, in);
    snprintf(wrapped, sizeof wrapped, "{\"k\":\"%s\"}", esc);
    CHECK(json_valid(wrapped), "escaped output not valid JSON: %s", wrapped);
    lmux_json_extract_string(wrapped, "k", back, sizeof back);
    CHECK(strcmp(back, in) == 0, "round trip mismatch");
    for (size_t cap = 1; cap < 40; cap++) {
        char *out = guard_buf(cap);
        lmux_json_escape(out, cap, in);
        CHECK(guard_ok(cap), "escape overflow cap=%zu", cap);
        snprintf(wrapped, sizeof wrapped, "{\"k\":\"%s\"}", out);
        CHECK(json_valid(wrapped), "truncated escape invalid at cap=%zu: %s", cap, out);
        size_t L = strlen(out);
        /* No dangling lead byte of the 3-byte euro sign. */
        CHECK(!(L >= 1 && (unsigned char)out[L - 1] == 0xE2), "split UTF-8 at cap=%zu", cap);
        CHECK(!(L >= 2 && (unsigned char)out[L - 2] == 0xE2 && (unsigned char)out[L - 1] == 0x82), "split UTF-8 at cap=%zu", cap);
    }
    PASS();
}

/* ── 2. Random fuzzing of the extractors ───────────────────────── */

static const char alphabet[] = "\"\\u{}[]:,0123456789abcdefABCDEF dnrtbf/\x01\xC3\xA9\xE2\x82\xAC\xF0";

static void random_json(char *buf, size_t len) {
    static const char *frags[] = { "{\"t\":\"", "\"t\":", "\\u", "\\ud83d", "\\ude00", "\\\"", "\\\\", "\":\"", "\"}", "{", "}" };
    size_t n = 0;
    while (n + 8 < len) {
        if (rnd() % 4 == 0) {
            const char *f = frags[rnd() % (sizeof frags / sizeof *frags)];
            size_t fl = strlen(f);
            memcpy(buf + n, f, fl);
            n += fl;
        } else {
            buf[n++] = alphabet[rnd() % (sizeof alphabet - 1)];
        }
        if (rnd() % 64 == 0) break;
    }
    buf[n] = 0;
}

static void test_fuzz_extractors(void) {
    TEST("random inputs never overrun the output buffer (20000 iterations)");
    char in[1024];
    for (int it = 0; it < 20000; it++) {
        random_json(in, 1 + rnd() % sizeof in);
        size_t cap = 1 + rnd() % 64;
        char *out = guard_buf(cap);
        lmux_json_extract_string(in, "t", out, cap);
        CHECK(guard_ok(cap), "overrun at iteration %d", it);
        int iv; bool bv;
        lmux_json_extract_int(in, "t", &iv);
        lmux_json_extract_bool(in, "t", &bv);
        lmux_json_find_toplevel_key(in, "t", NULL);
        out = guard_buf(cap);
        lmux_json_escape(out, cap, in);
        CHECK(guard_ok(cap), "escape overrun at iteration %d", it);
    }
    PASS();
}

/* ── 3. Dispatcher: responses and events stay valid JSON ───────── */

static void random_text(char *buf, size_t len) {
    static const char pool[] = "\"\\\n\r\t\x01\x1f{}:,  abcXYZ<>&'%$`";
    size_t n = 0, want = rnd() % (len - 1);
    while (n < want) {
        if (rnd() % 8 == 0 && n + 3 < want) { memcpy(buf + n, "\xE2\x82\xAC", 3); n += 3; continue; }
        buf[n++] = pool[rnd() % (sizeof pool - 1)];
    }
    buf[n] = 0;
    if (!buf[0]) { buf[0] = 'x'; buf[1] = 0; }
}

static void test_dispatch_outputs_valid_json(lmux_app *app) {
    TEST("hostile titles/names/text keep responses and events valid JSON");
    uint64_t cursor = lmux_app_event_seq(app);
    char txt[300], esc[2048], req[4096];
    for (int it = 0; it < 400; it++) {
        random_text(txt, sizeof txt);
        lmux_json_escape(esc, sizeof esc, txt);
        switch (it % 5) {
        case 0: snprintf(req, sizeof req, "{\"cmd\":\"workspace.create\",\"args\":{\"title\":\"%s\"}}", esc); break;
        case 1: snprintf(req, sizeof req, "{\"cmd\":\"notify\",\"args\":{\"text\":\"%s\",\"waiting\":false}}", esc); break;
        case 2: snprintf(req, sizeof req, "{\"cmd\":\"surface.rename\",\"args\":{\"id\":\"1\",\"title\":\"%s\"}}", esc); break;
        case 3: snprintf(req, sizeof req, "{\"cmd\":\"buffer.set\",\"args\":{\"data\":\"%s\"}}", esc); break;
        case 4: snprintf(req, sizeof req, "{\"cmd\":\"workspace.list\",\"args\":{}}"); break;
        }
        char *resp = lmux_dispatch_json(app, req);
        CHECK(json_valid(resp), "invalid response for %s\n  -> %s", req, resp ? resp : "(null)");
        free(resp);
        char *ev;
        while ((ev = lmux_event_next(app, &cursor)) != NULL) {
            int ok = json_valid(ev);
            if (!ok) { FAIL("invalid event after %s\n  -> %s", req, ev); free(ev); return; }
            free(ev);
        }
    }
    PASS();
}

/* Every command name the dispatcher matches, read from the source so a new
 * handler is covered automatically. The unit runner executes from the repo
 * root. Returns the count (0 if the source is not reachable). */
#define MAX_CMDS 512
static int load_dispatch_commands(char names[][64]) {
    FILE *f = fopen("src/core/model.c", "r");
    if (!f) return 0;
    static char line[4096];
    int n = 0;
    while (fgets(line, sizeof line, f) && n < MAX_CMDS) {
        for (char *p = line; (p = strstr(p, "strcmp(cmd, \"")) != NULL; ) {
            p += 13;
            char *q = strchr(p, '"');
            if (!q || q - p >= 63) break;
            char name[64];
            memcpy(name, p, (size_t)(q - p));
            name[q - p] = 0;
            int dup = 0;
            for (int i = 0; i < n; i++) if (!strcmp(names[i], name)) { dup = 1; break; }
            if (!dup && n < MAX_CMDS) strcpy(names[n++], name);
            p = q;
        }
    }
    fclose(f);
    return n;
}

/* Commands with effects outside this process: running hook or agent
 * commands, ssh/browser/cloud/companion calls, writing files, sending keys
 * into shells, quitting. Everything else is fair game. */
static int has_outside_effects(const char *c) {
    static const char *deny[] = {
        "hook", "agent", "spawn", "ssh", "browser", "cloud", "companion",
        "quit", "kill", "shutdown", "exit", "snapshot", "session.save",
        "session.restore", "save", "load", "import", "export", "file_explorer",
        "pipe", "respawn", "attach", "send", "config.set", "reload", "auth",
        "email", "calendar", "weather.refresh", "team.dispatch", "open",
        "vpn", "update", "install", "notify", "run", "exec", NULL };
    for (int i = 0; deny[i]; i++) if (strstr(c, deny[i])) return 1;
    return 0;
}

static void test_every_command_replies_valid_json(lmux_app *app) {
    TEST("every command replies valid JSON for hostile string arguments");
    static char names[MAX_CMDS][64];
    int n = load_dispatch_commands(names);
    CHECK(n > 150, "found only %d command names in src/core/model.c", n);
    /* Values that break naive JSON building or format strings. */
    static char longv[3200];
    memset(longv, '"', 1500);
    memset(longv + 1500, '\\', 1500);
    memcpy(longv + 3000, "\xE2\x82\xAC\n\t\x01", 6);
    longv[3006] = 0;
    const char *vals[] = {
        "q\"uote", "back\\slash\\", "ctl\x01\x1f\n\r\t", "%s%n%x%p",
        "\",\"ok\":true,\"x\":\"", "</script>\xE2\x82\xAC\xF0\x9F\x98\x80", longv };
    static const char *keys[] = {
        "title", "name", "text", "path", "cwd", "key", "value", "url",
        "command", "event", "format", "template", "query", "filter",
        "location", "scope", "data", "label", "provider", "size", "role",
        "platform", "device_id", "team_id", "vm_id", "group", "old_name",
        "new_name", "dir", "mode", "selector", "content", "message", "host",
        "user", "category", "id", "workspace_id", "surface_id", "pane_id" };
    uint64_t cursor = lmux_app_event_seq(app);
    static char esc[24000], req[64000];
    int tested = 0;
    for (int c = 0; c < n; c++) {
        if (has_outside_effects(names[c])) continue;
        for (size_t v = 0; v < sizeof vals / sizeof *vals; v++) {
            lmux_json_escape(esc, sizeof esc, vals[v]);
            int len = snprintf(req, sizeof req, "{\"cmd\":\"%s\",\"args\":{", names[c]);
            /* Stay under the dispatcher's 64 KiB request cap: add keys only
             * while the next one (plus the closing braces) still fits. */
            for (size_t k = 0; k < sizeof keys / sizeof *keys; k++) {
                int need = snprintf(NULL, 0, "%s\"%s\":\"%s\"", k ? "," : "", keys[k], esc);
                if ((size_t)len + (size_t)need + 3 >= 60000) break;
                len += snprintf(req + len, sizeof req - (size_t)len, "%s\"%s\":\"%s\"",
                                k ? "," : "", keys[k], esc);
            }
            snprintf(req + len, sizeof req - (size_t)len, "}}");
            char *resp = lmux_dispatch_json(app, req);
            if (!json_valid(resp)) {
                FAIL("%s: invalid JSON reply for value #%zu\n  -> %.300s",
                     names[c], v, resp ? resp : "(null)");
                free(resp);
                return;
            }
            free(resp);
            char *ev;
            while ((ev = lmux_event_next(app, &cursor)) != NULL) {
                int ok = json_valid(ev);
                if (!ok) { FAIL("%s: invalid event\n  -> %.300s", names[c], ev); free(ev); return; }
                free(ev);
            }
        }
        tested++;
    }
    printf("(%d commands) ", tested);
    PASS();
}

static void test_dispatch_random_garbage(lmux_app *app) {
    TEST("random request bodies never crash the dispatcher (3000 iterations)");
    static const char *cmds[] = { "workspace.create", "workspace.list", "surface.rename",
                                  "buffer.set", "pane.list", "nope.zzz" };
    char body[1024], req[1200];
    for (int it = 0; it < 3000; it++) {
        random_json(body, 1 + rnd() % sizeof body);
        snprintf(req, sizeof req, "{\"cmd\":\"%s\",\"args\":{\"title\":\"%s",
                 cmds[rnd() % (sizeof cmds / sizeof *cmds)], body);
        char *resp = lmux_dispatch_json(app, rnd() % 2 ? req : body);
        free(resp);
    }
    PASS();
}

int main(void) {
    printf("=== JSON layer tests ===\n");
    test_unicode_escape_at_buffer_end();
    test_trailing_backslash();
    test_surrogates();
    test_key_inside_value_not_matched();
    test_toplevel();
    test_escape();
    test_fuzz_extractors();

    /* Isolated HOME so no real snapshot/config is read or written. */
    char home[] = "/tmp/lmux-json-fuzz-XXXXXX";
    if (!mkdtemp(home)) { perror("mkdtemp"); return 1; }
    setenv("HOME", home, 1);
    setenv("XDG_DATA_HOME", home, 1);
    setenv("XDG_CONFIG_HOME", home, 1);
    char sock[128];
    snprintf(sock, sizeof sock, "%s/s.sock", home);
    lmux_app *app = lmux_app_new(sock);
    if (!app) { printf("FAIL: lmux_app_new\n"); return 1; }
    test_dispatch_outputs_valid_json(app);
    test_dispatch_random_garbage(app);
    test_every_command_replies_valid_json(app);
    lmux_app_free(app);
    char cmd[256];
    snprintf(cmd, sizeof cmd, "rm -rf '%s'", home);
    if (system(cmd) != 0) { /* best effort */ }

    printf("Tests: %d passed, %d failed, %d total\n", tests_pass, tests_fail, tests_run);
    return tests_fail ? 1 : 0;
}
