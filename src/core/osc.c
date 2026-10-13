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
 *   OSC 133 ; A|B|C|D<code> ST     - shell-integration command markers
 *                                   (ISS-0037): A = prompt start, B =
 *                                   prompt end / command start, C =
 *                                   command end, D = exit code
 *                                   (tmux oracle: input.c:3063+). The
 *                                   body carries the marker; callers
 *                                   feeding pty output detect it by the
 *                                   133-class completion flag.
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
    bool  last_was_osc133_valid; /* set while the most recently completed
                                   * sequence was an OSC 133 shell-integration
                                   * marker (ISS-0037). It is set when the ps
                                   * is recognized, preserved through the
                                   * completion reset, and cleared when a new
                                   * sequence starts ("]" after ESC) or when a
                                   * non-133 ps is recognized. */
    /* The 'body' is what we emit on completion. We separate the
     * OSC 99 'meta' portion (semicolon-delimited) from the text. */
    char  meta[256];
    size_t meta_len;
    size_t text_off;  /* index in buf where the text portion starts */

    /* OSC 133 completion queue (ISS-0037). The parser may complete
     * multiple sequences in a single feed call; the caller drains
     * this queue via lmux_osc_parser_pop_133(). */
    char   osc133_queue[8][32];
    size_t osc133_qhead, osc133_qtail;
};

lmux_osc_parser *lmux_osc_parser_new(void) {
    lmux_osc_parser *p = calloc(1, sizeof *p);
    return p;
}

void lmux_osc_parser_free(lmux_osc_parser *p) {
    free(p);
}

bool lmux_osc_parser_last_was_osc133(lmux_osc_parser *p) {
    return p && p->last_was_osc133_valid;
}

/* Pop the next completed OSC 133 marker from the parser's queue.
 * Returns true if a marker was popped into `out_marker` (caller must
 * provide a buffer of at least 32 bytes). Returns false if queue empty. */
bool lmux_osc_parser_pop_133(lmux_osc_parser *p, char *out_marker) {
    if (!p || p->osc133_qhead == p->osc133_qtail) return false;
    strncpy(out_marker, p->osc133_queue[p->osc133_qtail], 31);
    out_marker[31] = 0;
    p->osc133_qtail = (p->osc133_qtail + 1) % 8;
    return true;
}

/* Reset parser state for next sequence. Does NOT touch the state
 * field so callers can set state before/after calling this. */
static void osc_reset(lmux_osc_parser *p) {
    p->len       = 0;
    p->meta_len  = 0;
    p->text_off  = 0;
    p->waiting   = false;
    p->last_was_osc133_valid = false;
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
    /*
     * If text_off is 0 the ps separator (';') was consumed without
     * accumulating body bytes at an offset — the whole buffer is the
     * text (OSC 9/133 style). Otherwise the buffer layout is
     * [meta ';' body...]: NUL-terminate just past the separator so
     * the body starts at buf[text_off]. */
    if (p->text_off == 0) {
        *out_text   = p->buf;
        *out_waiting = p->waiting;
    } else {
        p->buf[p->text_off] = 0;
        *out_text   = p->buf + p->text_off;
        *out_waiting = p->waiting;
    }
}

bool lmux_osc_parser_feed(lmux_osc_parser *p, const char *bytes, size_t n,
                          const char **out_text, bool *out_waiting) {
    *out_text    = NULL;
    *out_waiting = false;
    bool any_completed = false;

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
            /* OSC <ps> ; - ps is the sequence selector. We care about
             * 9, 99, 777 (notifications) and 133 (shell-integration
             * command markers, ISS-0037; payload A|B|C|D<code> is the
             * body text). Anything else is dropped.
             *
             * SUS (0x1A) terminates the entire sequence here — tmux
             * treats it as a string terminator like BEL. The pty
             * translates it to BEL, but if it arrives raw (or the shell
             * emits SUS directly), it must not be accumulated into the
             * ps digits: that would produce a garbage ps that silently
             * drops the marker without flagging it as 133. */
            if (c == ';' || c == 0x07 || c == 0x1B) {
                /* Determine which ps we have. */
                int ps = atoi(p->buf);
                if (ps == 9 || ps == 99 || ps == 777 || ps == 133) {
                    /* Record whether this sequence is an OSC 133
                     * shell-integration marker (ISS-0037) so a caller
                     * feeding pty output can distinguish 133 completions
                     * from 9/99/777 notifications. The flag is set at the
                     * ps-recognition boundary and preserved through the
                     * completion; a non-133 ps or a new sequence start
                     * clears it. */
                    p->last_was_osc133_valid = (ps == 133);
                    /* Clear ps digits from buffer before entering body */
                    p->len = 0;
                    if (c == ';') {
                        /* ';' separates the ps selector from the body;
                         * the ps digits (already consumed) are discarded
                         * so the body is collected starting at buf[0].
                         * With text_off == 0, osc_finish() emits the
                         * whole body (OSC 9/99/777 text, or an OSC 133
                         * marker such as "B", "D0"). */
                        p->len      = 0;
                        p->text_off = 0;
                        p->state    = OSC_BODY;
                    } else if (c == 0x07) {
                        /* Empty body, BEL terminator. */
                        p->buf[0] = 0;
                        *out_text = p->buf;
                        *out_waiting = false;
                        osc_reset(p);
                        p->state = OSC_GROUND;
                        p->last_was_osc133_valid = (ps == 133);
                        any_completed = true;
                        if (ps == 133) {
                            size_t next = (p->osc133_qhead + 1) % 8;
                            if (next != p->osc133_qtail) {
                                p->osc133_queue[p->osc133_qhead][0] = 0;
                                p->osc133_qhead = next;
                            }
                        }
                        /* Continue processing remaining bytes in buffer. */
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
                bool was_133 = p->last_was_osc133_valid;
                osc_reset(p);
                /* osc_reset() returns the parser to a fresh-sequence state
                 * (and to the ground state so the ESC ] of the next
                 * sequence is read as a start, not as body content);
                 * restore the 133 flag so the completion is still
                 * distinguishable on the caller's poll. */
                p->last_was_osc133_valid = was_133;
                p->state = OSC_GROUND;
                any_completed = true;
                if (was_133 && *out_text) {
                    size_t next = (p->osc133_qhead + 1) % 8;
                    if (next != p->osc133_qtail) {
                        snprintf(p->osc133_queue[p->osc133_qhead], 32, "%s", *out_text);
                        p->osc133_qhead = next;
                    }
                }
                /* Continue processing remaining bytes in buffer. */
            } else if (c == 0x1B) {
                p->state = OSC_ST_ESC;
            } else if (c == 0x1A) {
                /* SUS (ASCII SUB, 0x1A) — some terminals use this to
                 * terminate OSC strings in lieu of ST (ESC \). Treat
                 * it exactly like BEL: emit the completion. */
                osc_finish(p, out_text, out_waiting);
                bool was_133 = p->last_was_osc133_valid;
                osc_reset(p);
                p->last_was_osc133_valid = was_133;
                p->state = OSC_GROUND;
                any_completed = true;
                if (was_133 && *out_text) {
                    size_t next = (p->osc133_qhead + 1) % 8;
                    if (next != p->osc133_qtail) {
                        snprintf(p->osc133_queue[p->osc133_qhead], 32, "%s", *out_text);
                        p->osc133_qhead = next;
                    }
                }
                /* Continue processing remaining bytes in buffer. */
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
                bool was_133 = p->last_was_osc133_valid;
                osc_reset(p);
                p->last_was_osc133_valid = was_133;
                p->state = OSC_GROUND;
                any_completed = true;
                if (was_133 && *out_text) {
                    size_t next = (p->osc133_qhead + 1) % 8;
                    if (next != p->osc133_qtail) {
                        snprintf(p->osc133_queue[p->osc133_qhead], 32, "%s", *out_text);
                        p->osc133_qhead = next;
                    }
                }
                /* Continue processing remaining bytes in buffer. */
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
    return any_completed;
}
