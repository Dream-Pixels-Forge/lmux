/*
 * lmux_browser.h — In-App Browser (WebKit2GTK integration)
 *
 * Provides a scriptable browser pane that can be split alongside terminals.
 * API inspired by cmux's WKWebView integration (Vercel agent-browser).
 */

#ifndef LMUX_BROWSER_H
#define LMUX_BROWSER_H

#include <stdbool.h>
#include <stddef.h>

/* Forward declare — actual type defined in browser.c */
typedef struct lmux_browser lmux_browser;

/* Lifecycle */
lmux_browser *lmux_browser_new(void);
void          lmux_browser_free(lmux_browser *b);

/* Navigation */
bool lmux_browser_navigate(lmux_browser *b, const char *url);
bool lmux_browser_go_back(lmux_browser *b);
bool lmux_browser_go_forward(lmux_browser *b);
bool lmux_browser_reload(lmux_browser *b);
bool lmux_browser_stop(lmux_browser *b);

/* Content access */
char *lmux_browser_get_url(lmux_browser *b);
char *lmux_browser_get_title(lmux_browser *b);
char *lmux_browser_get_snapshot(lmux_browser *b);  /* accessibility tree */
char *lmux_browser_get_html(lmux_browser *b);       /* full HTML */

/* Interaction */
bool lmux_browser_click(lmux_browser *b, const char *selector);
bool lmux_browser_fill(lmux_browser *b, const char *selector, const char *value);
bool lmux_browser_select(lmux_browser *b, const char *selector, const char *value);
bool lmux_browser_press_key(lmux_browser *b, const char *key);

/* JavaScript */
char *lmux_browser_evaluate(lmux_browser *b, const char *js);

/* Screenshot */
char *lmux_browser_screenshot(lmux_browser *b, const char *path);

/* State */
bool lmux_browser_is_loading(lmux_browser *b);
double lmux_browser_get_progress(lmux_browser *b);

#endif /* LMUX_BROWSER_H */
