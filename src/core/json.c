/**
 * json.c — Minimal, bounds-safe JSON field helpers. See lmux_json.h.
 */
#include "lmux_json.h"

#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int is_ws(char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r';
}

static const char *skip_ws(const char *p) {
    while (is_ws(*p)) p++;
    return p;
}

/* p points just after an opening quote. Returns a pointer to the closing
 * quote, or to the NUL terminator if the string is unterminated. */
static const char *string_end(const char *p) {
    while (*p && *p != '"') {
        if (*p == '\\') {
            p++;
            if (!*p) break;
        }
        p++;
    }
    return p;
}

/* Shared scanner. max_depth < 0 means "any depth". */
static const char *find_key(const char *json, const char *key, int max_depth,
                            int *depth_out) {
    if (!json || !key) return NULL;
    const size_t klen = strlen(key);
    int depth = 0;
    for (const char *p = json; *p; p++) {
        if (*p == '"') {
            const char *start = p + 1;
            const char *end = string_end(start);
            if (!*end) return NULL;               /* unterminated */
            const char *after = skip_ws(end + 1);
            if (*after == ':' &&
                (max_depth < 0 || depth == max_depth) &&
                (size_t)(end - start) == klen &&
                memcmp(start, key, klen) == 0) {
                if (depth_out) *depth_out = depth;
                return skip_ws(after + 1);
            }
            p = end;                              /* skip the whole token */
            continue;
        }
        if (*p == '{' || *p == '[') depth++;
        else if (*p == '}' || *p == ']') depth--;
    }
    return NULL;
}

const char *lmux_json_find_key(const char *json, const char *key) {
    return find_key(json, key, -1, NULL);
}

const char *lmux_json_find_toplevel_key(const char *json, const char *key,
                                        int *depth_out) {
    return find_key(json, key, 1, depth_out);
}

/* Parse exactly four hex digits. Returns -1 (and consumes nothing) if any of
 * them is missing or invalid; stops at the NUL terminator. */
static long parse_hex4(const char *v) {
    long cp = 0;
    for (int i = 0; i < 4; i++) {
        char c = v[i];
        int d = (c >= '0' && c <= '9') ? c - '0' :
                (c >= 'a' && c <= 'f') ? c - 'a' + 10 :
                (c >= 'A' && c <= 'F') ? c - 'A' + 10 : -1;
        if (d < 0) return -1;
        cp = (cp << 4) | d;
    }
    return cp;
}

static size_t utf8_encode(uint32_t cp, char buf[4]) {
    if (cp <= 0x7F) {
        buf[0] = (char)cp;
        return 1;
    }
    if (cp <= 0x7FF) {
        buf[0] = (char)(0xC0 | (cp >> 6));
        buf[1] = (char)(0x80 | (cp & 0x3F));
        return 2;
    }
    if (cp <= 0xFFFF) {
        buf[0] = (char)(0xE0 | (cp >> 12));
        buf[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
        buf[2] = (char)(0x80 | (cp & 0x3F));
        return 3;
    }
    buf[0] = (char)(0xF0 | (cp >> 18));
    buf[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
    buf[2] = (char)(0x80 | ((cp >> 6) & 0x3F));
    buf[3] = (char)(0x80 | (cp & 0x3F));
    return 4;
}

bool lmux_json_decode_string(const char *v, char *out, size_t cap) {
    if (!v || *v != '"' || !out || cap == 0) return false;
    v++;
    size_t n = 0;
    while (*v && *v != '"') {
        char tmp[4];
        size_t len = 1;
        if (*v != '\\') {
            tmp[0] = *v++;
        } else {
            v++;
            if (!*v) break;                       /* lone trailing backslash */
            char c = *v++;
            switch (c) {
            case 'b': tmp[0] = '\b'; break;
            case 'f': tmp[0] = '\f'; break;
            case 'n': tmp[0] = '\n'; break;
            case 'r': tmp[0] = '\r'; break;
            case 't': tmp[0] = '\t'; break;
            case 'u': {
                long cp = parse_hex4(v);
                if (cp < 0) {
                    cp = 0xFFFD;
                } else {
                    v += 4;
                    if (cp >= 0xD800 && cp <= 0xDBFF) {
                        long lo = (v[0] == '\\' && v[1] == 'u') ? parse_hex4(v + 2) : -1;
                        if (lo >= 0xDC00 && lo <= 0xDFFF) {
                            cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                            v += 6;
                        } else {
                            cp = 0xFFFD;
                        }
                    } else if (cp >= 0xDC00 && cp <= 0xDFFF) {
                        cp = 0xFFFD;
                    }
                }
                if (cp == 0) continue;            /* keep a valid C string */
                len = utf8_encode((uint32_t)cp, tmp);
                break;
            }
            default:  tmp[0] = c; break;          /* \" \\ \/ and unknowns */
            }
        }
        if (n + len >= cap) break;                /* never split a sequence */
        memcpy(out + n, tmp, len);
        n += len;
    }
    out[n] = 0;
    return true;
}

bool lmux_json_extract_string(const char *json, const char *key,
                              char *out, size_t cap) {
    const char *v = lmux_json_find_key(json, key);
    if (!v || *v != '"') return false;
    return lmux_json_decode_string(v, out, cap);
}

bool lmux_json_extract_int(const char *json, const char *key, int *out) {
    const char *v = lmux_json_find_key(json, key);
    if (!v || !out) return false;
    if (*v != '-' && (*v < '0' || *v > '9')) return false;
    errno = 0;
    long x = strtol(v, NULL, 10);
    if (x > INT_MAX) x = INT_MAX;
    if (x < INT_MIN) x = INT_MIN;
    *out = (int)x;
    return true;
}

bool lmux_json_extract_bool(const char *json, const char *key, bool *out) {
    const char *v = lmux_json_find_key(json, key);
    if (!v || !out) return false;
    if (strncmp(v, "true", 4) == 0) { *out = true; return true; }
    if (strncmp(v, "false", 5) == 0) { *out = false; return true; }
    return false;
}

char *lmux_json_escape(char *out, size_t cap, const char *in) {
    if (!out || cap == 0) return out;
    if (!in) { out[0] = 0; return out; }
    size_t j = 0;
    bool truncated = false;
    for (const unsigned char *p = (const unsigned char *)in; *p; p++) {
        char tmp[8];
        size_t len;
        switch (*p) {
        case '"':  memcpy(tmp, "\\\"", 2); len = 2; break;
        case '\\': memcpy(tmp, "\\\\", 2); len = 2; break;
        case '\n': memcpy(tmp, "\\n", 2);  len = 2; break;
        case '\r': memcpy(tmp, "\\r", 2);  len = 2; break;
        case '\t': memcpy(tmp, "\\t", 2);  len = 2; break;
        case '\b': memcpy(tmp, "\\b", 2);  len = 2; break;
        case '\f': memcpy(tmp, "\\f", 2);  len = 2; break;
        default:
            if (*p < 0x20 || *p == 0x7F) {
                len = (size_t)snprintf(tmp, sizeof tmp, "\\u%04x", *p);
            } else {
                tmp[0] = (char)*p;
                len = 1;
            }
        }
        if (j + len >= cap) { truncated = true; break; }
        memcpy(out + j, tmp, len);
        j += len;
    }
    if (truncated) {
        /* Don't leave half a UTF-8 sequence at the end. */
        size_t k = j, cont = 0;
        while (k > 0 && cont < 3 && ((unsigned char)out[k - 1] & 0xC0) == 0x80) {
            k--;
            cont++;
        }
        if (k > 0) {
            unsigned char lead = (unsigned char)out[k - 1];
            size_t need = (lead & 0xE0) == 0xC0 ? 2 :
                          (lead & 0xF0) == 0xE0 ? 3 :
                          (lead & 0xF8) == 0xF0 ? 4 : 1;
            if (need > 1 && cont + 1 < need) j = k - 1;
        }
    }
    out[j] = 0;
    return out;
}

const char *lmux_json_esc(const char *in) {
    static _Thread_local char bufs[LMUX_JSON_ESC_SLOTS][LMUX_JSON_ESC_SLOT_SIZE];
    static _Thread_local unsigned idx;
    char *b = bufs[idx++ % LMUX_JSON_ESC_SLOTS];
    return lmux_json_escape(b, sizeof bufs[0], in);
}
