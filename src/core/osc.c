/*
 * osc.c - OSC 9 / 99 / 777 terminal notification parser.
 *
 * The parser is a small state machine that consumes a stream of
 * terminal bytes and emits complete notification bodies. It is
 * designed to be fed byte-by-byte from a VTE/Ghostty callback or in
 * larger chunks from a pty reader.
 *
 * Supported sequences (per the PRD section 3.3):
 *   OSC 9 ; <text> ST              - simple notify, body is <text>
 *   OSC 99 ; <meta> ; <text> ST    - meta is a comma-separated list;
 *                                   'i' = waiting-for-input
 *   OSC 777 ; notify ; <text> ST   - rxvt-style notify
 *
 * String-terminator (ST) is recognized as either ESC \ (the standard
 * 7-bit form) or BEL (0x07) for compatibility with iTerm2 / many
 * real-world agents. The parser is permissive about leading CSI
 * introductions in case the host's terminal layer stripped ESC itself.
 *
 * The parser holds onto the last decoded body in its own buffer; the
 * caller must consume out_text before feeding more bytes (we keep the
 * buffer stable until the next feed call to make single-shot use safe).
 */
#include "lmux.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

enum osc_state {
    OSC_GROUND = 0,
    OSC_ESC,        /* saw ESC */
    OSC_OSC,        /* saw ESC ] */
    OSC_BODY,       /* collecting body */
    OSC_STRING,      /* inside a quoted OSC 99 meta block */
    OSC_ST_ESC,     /* saw ESC inside body, possible ST */
};

struct lmux_osc_parser {
    enum osc_state state;
    char  buf[2048];
    size_t len;
    bool  waiting;
    /* The 'body' is what we emit on completion. We separate the
     * OSC 99 'meta' portion (semicolon-delimited) from the text. */
    char  meta[256];
    size_t meta_len;
    size_t text_off;  /* index in buf where the text portion starts */
};

lmux_osc_parser *lmux_osc_parser_new(void) {
    lmux_osc_parser *p = calloc(1, sizeof *p);
    return p;
}

void lmux_osc_parser_free(lmux_osc_parser *p) {
    free(p);
}

/* Reset parser state for next sequence. Does NOT touch the state
 * field so callers can set state before/after calling this. */
static void osc_reset(lmux_osc_parser *p) {
    p->len       = 0;
    p->meta_len  = 0;
    p->text_off  = 0;
    p->waiting   = false;
}

static void osc_finish(lmux_osc_parser *p, const char **out_text,
                       bool *out_waiting) {
    p->buf[p->len] = 0;
    /* NUL-terminate the meta portion. */
    if (p->text_off > 0) {
        p->buf[p->text_off - 1] = 0; /* overwrite the separator ';' */
    }
    /* Parse the meta (OSC 99): look for 'i' to mean waiting-for-input. */
    if (p->meta_len > 0) {
        for (size_t i = 0; i < p->meta_len; i++) {
            if (p->meta[i] == 'i') {
                p->waiting = true;
                break;
            }
        }
    }
    /* If text_off is 0 there was no meta separator; treat whole buf
     * as text. */
    if (p->text_off == 0) {
        *out_text   = p->buf;
        *out_waiting = p->waiting;
    } else {
        *out_text   = p->buf + p->text_off;
        *out_waiting = p->waiting;
    }
}

bool lmux_osc_parser_feed(lmux_osc_parser *p, const char *bytes, size_t n,
                          const char **out_text, bool *out_waiting) {
    *out_text    = NULL;
    *out_waiting = false;

    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)bytes[i];

        switch (p->state) {
        case OSC_GROUND:
            if (c == 0x1B) { p->state = OSC_ESC; }
            break;

        case OSC_ESC:
            if (c == ']') {
                p->state = OSC_OSC;
                osc_reset(p);
            } else {
                p->state = OSC_GROUND;
            }
            break;

        case OSC_OSC:
            /* OSC <ps> ; - ps is the sequence selector. We only care
             * about 9, 99, 777. Anything else is dropped. */
            if (c == ';' || c == 0x07 || c == 0x1B) {
                /* Determine which ps we have. */
                int ps = atoi(p->buf);
                if (ps == 9 || ps == 99 || ps == 777) {
                    /* Clear ps digits from buffer before entering body */
                    p->len = 0;
                    if (c == ';') {
                        p->text_off = 0;
                        p->state    = OSC_BODY;
                    } else if (c == 0x07) {
                        /* Empty body, BEL terminator. */
                        p->buf[0] = 0;
                        *out_text = p->buf;
                        *out_waiting = false;
                        osc_reset(p);
                        return true;
                    } else {
                        /* ESC - expect \ as ST */
                        p->state = OSC_ST_ESC;
                    }
                } else {
                    /* Not a notify sequence we recognize. Drop. */
                    p->state = OSC_GROUND;
                }
            } else {
                if (p->len < sizeof(p->buf) - 1) {
                    p->buf[p->len++] = c;
                }
            }
            break;

        case OSC_BODY:
            if (c == 0x07) {
                osc_finish(p, out_text, out_waiting);
                osc_reset(p);
                return true;
            } else if (c == 0x1B) {
                p->state = OSC_ST_ESC;
            } else if (c == ';' && p->text_off == 0) {
                /* OSC 99 separator between meta and text. Move what
                 * we have so far into the meta buffer. */
                if (p->len < sizeof(p->meta)) {
                    memcpy(p->meta, p->buf, p->len);
                    p->meta_len = p->len;
                }
                p->text_off = p->len + 1;  /* point past the ';' */
            } else {
                if (p->len < sizeof(p->buf) - 1) {
                    p->buf[p->len++] = c;
                }
            }
            break;

        case OSC_ST_ESC:
            if (c == '\\') {
                osc_finish(p, out_text, out_waiting);
                osc_reset(p);
                return true;
            } else {
                /* Not a valid ST; treat as content. */
                p->state = OSC_BODY;
                if (p->len < sizeof(p->buf) - 1) {
                    p->buf[p->len++] = c;
                }
            }
            break;

        case OSC_STRING:
            /* Reserved for future quoted strings. */
            break;
        }
    }
    return false;
}
