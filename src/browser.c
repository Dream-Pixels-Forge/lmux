/*
 * browser.c — In-App Browser (stub implementation)
 *
 * The daemon acknowledges browser commands; actual WebKit rendering
 * happens in the GUI (lmux-gui). This stub compiles without WebKit
 * and handles the API surface for null-safety and command routing.
 *
 * Full WebKit2GTK integration lives in the GUI layer.
 */

#define _POSIX_C_SOURCE 200809L
#include "lmux_browser.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Opaque type — actual implementation in GUI */
struct lmux_browser {
    char current_url[2048];
    char current_title[512];
    bool loading;
    double progress;
};

/* ------------------------------------------------------------------ */
/* Lifecycle                                                           */
/* ------------------------------------------------------------------ */

lmux_browser *lmux_browser_new(void) {
    lmux_browser *b = calloc(1, sizeof(*b));
    if (!b) return NULL;
    strncpy(b->current_url, "about:blank", sizeof(b->current_url) - 1);
    return b;
}

void lmux_browser_free(lmux_browser *b) {
    free(b);
}

/* ------------------------------------------------------------------ */
/* Navigation                                                          */
/* ------------------------------------------------------------------ */

bool lmux_browser_navigate(lmux_browser *b, const char *url) {
    if (!b || !url) return false;
    snprintf(b->current_url, sizeof(b->current_url), "%s", url);
    b->loading = true;
    b->progress = 0.0;
    return true;
}

bool lmux_browser_go_back(lmux_browser *b) {
    if (!b) return false;
    return true;  /* stub */
}

bool lmux_browser_go_forward(lmux_browser *b) {
    if (!b) return false;
    return true;  /* stub */
}

bool lmux_browser_reload(lmux_browser *b) {
    if (!b) return false;
    b->loading = true;
    return true;
}

bool lmux_browser_stop(lmux_browser *b) {
    if (!b) return false;
    b->loading = false;
    return true;
}

/* ------------------------------------------------------------------ */
/* Content access                                                      */
/* ------------------------------------------------------------------ */

char *lmux_browser_get_url(lmux_browser *b) {
    if (!b) return NULL;
    return strdup(b->current_url);
}

char *lmux_browser_get_title(lmux_browser *b) {
    if (!b) return NULL;
    return strdup(b->current_title);
}

char *lmux_browser_get_snapshot(lmux_browser *b) {
    if (!b) return NULL;
    /* Returns accessibility tree — actual impl in GUI */
    char buf[4096];
    snprintf(buf, sizeof(buf),
        "{\"title\":\"%s\",\"url\":\"%s\",\"status\":\"stub\"}",
        b->current_title, b->current_url);
    return strdup(buf);
}

char *lmux_browser_get_html(lmux_browser *b) {
    if (!b) return NULL;
    return strdup("<html><body>stub</body></html>");
}

/* ------------------------------------------------------------------ */
/* Interaction                                                         */
/* ------------------------------------------------------------------ */

bool lmux_browser_click(lmux_browser *b, const char *selector) {
    if (!b || !selector) return false;
    return true;  /* stub — actual impl in GUI */
}

bool lmux_browser_fill(lmux_browser *b, const char *selector, const char *value) {
    if (!b || !selector || !value) return false;
    return true;  /* stub */
}

bool lmux_browser_select(lmux_browser *b, const char *selector, const char *value) {
    if (!b || !selector || !value) return false;
    return true;  /* stub */
}

bool lmux_browser_press_key(lmux_browser *b, const char *key) {
    if (!b || !key) return false;
    return true;  /* stub */
}

/* ------------------------------------------------------------------ */
/* JavaScript evaluation                                               */
/* ------------------------------------------------------------------ */

char *lmux_browser_evaluate(lmux_browser *b, const char *js) {
    if (!b || !js) return NULL;
    return strdup("\"stub\"");
}

/* ------------------------------------------------------------------ */
/* Screenshot                                                          */
/* ------------------------------------------------------------------ */

char *lmux_browser_screenshot(lmux_browser *b, const char *path) {
    if (!b) return NULL;
    const char *out = path ? path : "/tmp/lmux-screenshot.png";
    return strdup(out);
}

/* ------------------------------------------------------------------ */
/* State                                                               */
/* ------------------------------------------------------------------ */

bool lmux_browser_is_loading(lmux_browser *b) {
    if (!b) return false;
    return b->loading;
}

double lmux_browser_get_progress(lmux_browser *b) {
    if (!b) return 0.0;
    return b->progress;
}
