/*
 * format.c - Format string expander for lmux status line and pane formatting.
 *
 * Supports:
 *   - Variable expansion: #{variable}
 *   - Conditionals: #{?condition:true:false}
 *   - Window list loop: #{W:format}
 *
 * 10 core variables mapped to lmux model:
 *   pane_id, pane_title, pane_width, pane_height
 *   session_id, session_name
 *   surface_id, surface_title
 *   window_id, window_name (aliases for surface)
 */
#define _POSIX_C_SOURCE 200809L
#define _GNU_SOURCE

#include "lmux.h"
#include <ctype.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Include internal struct definitions from model.c */
struct lmux_pane {
    lmux_id       id;
    lmux_workspace *owner;
    lmux_surface  *surface;
    char          kind[16];
    char          command[512];
    char          url[1024];
    bool          focused;
    int           pty_fd;
    pid_t         child_pid;
    /* PTY size (set by lmux_pane_pty_set_size, for format expander) */
    int           pty_cols;
    int           pty_rows;
    bool          copy_mode_active;
    int           copy_cursor_row;
    int           copy_cursor_col;
    int           copy_sel_start_row;
    int           copy_sel_start_col;
    bool          copy_selecting;
    char         *sb;
    size_t        sb_len;
    size_t        sb_cap;
    bool          cmd_running;
    int           last_cmd_exit;
    int           pending_cmd_exit;
    long          last_cmd_duration_ms;
    long          last_cmd_started_ms;
    long          cmd_started_mono_ms;
    void         *osc;
};

struct lmux_surface {
    lmux_id        id;
    lmux_workspace *owner;
    char           title[128];
    bool           focused;
    struct { void **items; size_t len; size_t cap; } panes;
    bool           canvas_mode;
    int            canvas_pane_x[64];
    int            canvas_pane_y[64];
    int            canvas_pane_w[64];
    int            canvas_pane_h[64];
    int            canvas_pane_z[64];
    int            canvas_pane_count;
};

struct lmux_workspace {
    struct lmux_app *app;
    lmux_id        id;
    char           title[256];
    char           cwd[1024];
    char           git_branch[128];
    char           pr_label[64];
    char           ports[256];
    char           last_notify[512];
    bool           unread;
    bool           waiting;
    bool           focused;
    char           ssh_host[256];
    int            ssh_port;
    bool           ssh_forward_agent;
    struct { void **items; size_t len; size_t cap; } surfaces;
    struct { void **items; size_t len; size_t cap; } env_vars;
    struct { void **items; size_t len; size_t cap; } auth_tokens;
};

/* Maximum recursion depth for nested conditionals/loops */
#define FORMAT_MAX_DEPTH 16

/* Forward declarations */
static bool format_expand_recursive(struct lmux_app *app, struct lmux_workspace *ws,
                                     struct lmux_surface *surf, struct lmux_pane *pane,
                                     const char *fmt, char *out, size_t cap,
                                     size_t *out_len, int depth);
static const char *get_variable_value(struct lmux_app *app, struct lmux_workspace *ws,
                                       struct lmux_surface *surf, struct lmux_pane *pane,
                                       const char *var_name, char *buf, size_t cap);
static bool is_truthy(const char *value);
static bool format_parse_conditional(const char **fmt, char *cond, size_t cond_cap,
                                      char *true_branch, size_t true_cap,
                                      char *false_branch, size_t false_cap);
static bool format_parse_window_loop(const char **fmt, char *loop_fmt, size_t loop_cap);

/* Public API */
bool lmux_format_expand(lmux_app *app, lmux_pane *pane,
                        const char *fmt, char *out, size_t cap) {
    if (!app || !fmt || !out || cap == 0) return false;
    if (!pane) return false;

    struct lmux_workspace *ws = pane->owner;
    struct lmux_surface *surf = pane->surface;
    if (!ws || !surf) return false;

    size_t out_len = 0;
    out[0] = '\0';
    return format_expand_recursive(app, ws, surf, pane, fmt, out, cap, &out_len, 0);
}

/* Main recursive expansion function */
static bool format_expand_recursive(struct lmux_app *app, struct lmux_workspace *ws,
                                     struct lmux_surface *surf, struct lmux_pane *pane,
                                     const char *fmt, char *out, size_t cap,
                                     size_t *out_len, int depth) {
    if (depth > FORMAT_MAX_DEPTH) return false;
    if (*out_len >= cap - 1) return false;

    const char *p = fmt;
    while (*p) {
        if (*p == '#' && *(p + 1) == '{') {
            p += 2; /* Skip #{ */
            
            /* Check for conditional: #{?...} */
            if (*p == '?') {
                p++; /* Skip ? */
                char cond[256] = {0};
                char true_branch[1024] = {0};
                char false_branch[1024] = {0};
                
                if (!format_parse_conditional(&p, cond, sizeof cond,
                                              true_branch, sizeof true_branch,
                                              false_branch, sizeof false_branch)) {
                    return false;
                }
                
                /* Evaluate condition */
                char cond_value[64] = {0};
                const char *val = get_variable_value(app, ws, surf, pane, cond, cond_value, sizeof cond_value);
                bool truthy = is_truthy(val);
                
                /* Expand the chosen branch */
                const char *branch = truthy ? true_branch : false_branch;
                char branch_result[1024] = {0};
                size_t branch_len = 0;
                if (!format_expand_recursive(app, ws, surf, pane, branch, branch_result,
                                              sizeof branch_result, &branch_len, depth + 1)) {
                    return false;
                }
                
                /* Append branch result to output */
                size_t n = strlen(branch_result);
                if (*out_len + n >= cap - 1) return false;
                memcpy(out + *out_len, branch_result, n);
                *out_len += n;
                out[*out_len] = '\0';
                continue;
            }
            
            /* Check for window list loop: #{W:...} */
            if (*p == 'W' && *(p + 1) == ':') {
                p += 2; /* Skip W: */
                char loop_fmt[1024] = {0};
                
                if (!format_parse_window_loop(&p, loop_fmt, sizeof loop_fmt)) {
                    return false;
                }
                
                /* Iterate over all surfaces in workspace */
                for (size_t i = 0; i < ws->surfaces.len; i++) {
                    struct lmux_surface *loop_surf = ws->surfaces.items[i];
                    if (!loop_surf) continue;
                    
                    /* For the loop, we need a pane on this surface.
                     * Use the focused pane, or the first pane if none focused. */
                    struct lmux_pane *loop_pane = NULL;
                    for (size_t j = 0; j < loop_surf->panes.len; j++) {
                        struct lmux_pane *cp = loop_surf->panes.items[j];
                        if (cp && cp->focused) {
                            loop_pane = cp;
                            break;
                        }
                    }
                    if (!loop_pane && loop_surf->panes.len > 0) {
                        loop_pane = loop_surf->panes.items[0];
                    }
                    
                    /* If no pane on this surface, use the original pane with loop surface context */
                    if (!loop_pane) {
                        loop_pane = pane;
                    }
                    
                    char loop_result[1024] = {0};
                    size_t loop_len = 0;
                    if (!format_expand_recursive(app, ws, loop_surf, loop_pane, loop_fmt,
                                                  loop_result, sizeof loop_result,
                                                  &loop_len, depth + 1)) {
                        return false;
                    }
                    
                    size_t n = strlen(loop_result);
                    if (*out_len + n >= cap - 1) return false;
                    memcpy(out + *out_len, loop_result, n);
                    *out_len += n;
                    out[*out_len] = '\0';
                }
                continue;
            }
            
            /* Regular variable: #{variable} */
            const char *var_start = p;
            while (*p && *p != '}') p++;
            if (*p != '}') return false; /* Unterminated variable */
            
            size_t var_len = p - var_start;
            if (var_len >= 64) return false; /* Variable name too long */
            
            char var_name[64];
            memcpy(var_name, var_start, var_len);
            var_name[var_len] = '\0';
            p++; /* Skip } */
            
            char var_value[256] = {0};
            const char *val = get_variable_value(app, ws, surf, pane, var_name, var_value, sizeof var_value);
            
            size_t n = strlen(val);
            if (*out_len + n >= cap - 1) return false;
            memcpy(out + *out_len, val, n);
            *out_len += n;
            out[*out_len] = '\0';
            continue;
        }
        
        /* Regular character */
        if (*out_len >= cap - 1) return false;
        out[(*out_len)++] = *p++;
        out[*out_len] = '\0';
    }
    
    return true;
}

/* Get the value of a variable */
static const char *get_variable_value(struct lmux_app *app, struct lmux_workspace *ws,
                                       struct lmux_surface *surf, struct lmux_pane *pane,
                                       const char *var_name, char *buf, size_t cap) {
    static char static_buf[256];
    char *out = buf ? buf : static_buf;
    size_t out_cap = cap ? cap : sizeof static_buf;
    
    (void)app; /* Unused for now, but kept for future extensibility */
    
    if (strcmp(var_name, "pane_id") == 0) {
        snprintf(out, out_cap, "%u", pane->id);
    } else if (strcmp(var_name, "pane_title") == 0) {
        if (strcmp(pane->kind, "browser") == 0) {
            snprintf(out, out_cap, "%s", pane->url);
        } else {
            snprintf(out, out_cap, "%s", pane->command);
        }
    } else if (strcmp(var_name, "pane_width") == 0) {
        /* Use stored PTY size */
        snprintf(out, out_cap, "%d", pane->pty_cols);
    } else if (strcmp(var_name, "pane_height") == 0) {
        snprintf(out, out_cap, "%d", pane->pty_rows);
    } else if (strcmp(var_name, "session_id") == 0) {
        snprintf(out, out_cap, "%u", ws->id);
    } else if (strcmp(var_name, "session_name") == 0) {
        snprintf(out, out_cap, "%s", ws->title);
    } else if (strcmp(var_name, "surface_id") == 0) {
        snprintf(out, out_cap, "%u", surf->id);
    } else if (strcmp(var_name, "surface_title") == 0) {
        snprintf(out, out_cap, "%s", surf->title);
    } else if (strcmp(var_name, "window_id") == 0) {
        snprintf(out, out_cap, "%u", surf->id); /* alias for surface_id */
    } else if (strcmp(var_name, "window_name") == 0) {
        snprintf(out, out_cap, "%s", surf->title); /* alias for surface_title */
    } else if (strcmp(var_name, "pane_focused") == 0) {
        snprintf(out, out_cap, "%s", pane->focused ? "1" : "0");
    } else if (strcmp(var_name, "window_active") == 0) {
        snprintf(out, out_cap, "%s", surf->focused ? "1" : "0");
    } else if (strcmp(var_name, "buffer_count") == 0) {
        snprintf(out, out_cap, "%zu", lmux_buffer_count(app));
    } else if (strncmp(var_name, "buffer_", 7) == 0 && 
               strlen(var_name) > 13 && 
               strcmp(var_name + strlen(var_name) - 5, "_size") == 0) {
        /* #{buffer_<name>_size} - extract buffer name between "buffer_" and "_size" */
        size_t name_len = strlen(var_name) - 12; /* 7 for "buffer_" + 5 for "_size" */
        if (name_len > 0 && name_len < 64) {
            char buf_name[64];
            memcpy(buf_name, var_name + 7, name_len);
            buf_name[name_len] = '\0';
            size_t sz = lmux_buffer_get_size(app, buf_name);
            if (sz != SIZE_MAX) {
                snprintf(out, out_cap, "%zu", sz);
            } else {
                out[0] = '\0';
            }
        } else {
            out[0] = '\0';
        }
    } else {
        /* Unknown variable - return empty string */
        out[0] = '\0';
    }
    
    return out;
}

/* Check if a value is truthy */
static bool is_truthy(const char *value) {
    if (!value || !*value) return false;
    if (strcmp(value, "0") == 0) return false;
    if (strcasecmp(value, "false") == 0) return false;
    if (strcasecmp(value, "no") == 0) return false;
    return true;
}

/* Parse a conditional: #{?condition:true:false} */
static bool format_parse_conditional(const char **fmt, char *cond, size_t cond_cap,
                                      char *true_branch, size_t true_cap,
                                      char *false_branch, size_t false_cap) {
    const char *p = *fmt;
    
    /* Parse condition (until :) */
    size_t cond_len = 0;
    while (*p && *p != ':' && cond_len < cond_cap - 1) {
        cond[cond_len++] = *p++;
    }
    cond[cond_len] = '\0';
    if (*p != ':') return false;
    p++; /* Skip : */
    
    /* Parse true branch (until : or }) */
    size_t true_len = 0;
    int brace_depth = 0;
    while (*p) {
        if (*p == '#' && *(p + 1) == '{') {
            brace_depth++;
            if (true_len < true_cap - 1) true_branch[true_len++] = *p++;
            if (true_len < true_cap - 1) true_branch[true_len++] = *p++;
            continue;
        }
        if (*p == '}' && brace_depth > 0) {
            brace_depth--;
            if (true_len < true_cap - 1) true_branch[true_len++] = *p++;
            continue;
        }
        if (*p == ':' && brace_depth == 0) break;
        if (*p == '}' && brace_depth == 0) break;
        if (true_len < true_cap - 1) true_branch[true_len++] = *p++;
    }
    true_branch[true_len] = '\0';
    
    if (*p == '}') {
        /* No false branch */
        false_branch[0] = '\0';
        *fmt = p + 1;
        return true;
    }
    
    if (*p != ':') return false;
    p++; /* Skip : */
    
    /* Parse false branch (until }) */
    size_t false_len = 0;
    brace_depth = 0;
    while (*p) {
        if (*p == '#' && *(p + 1) == '{') {
            brace_depth++;
            if (false_len < false_cap - 1) false_branch[false_len++] = *p++;
            if (false_len < false_cap - 1) false_branch[false_len++] = *p++;
            continue;
        }
        if (*p == '}' && brace_depth > 0) {
            brace_depth--;
            if (false_len < false_cap - 1) false_branch[false_len++] = *p++;
            continue;
        }
        if (*p == '}' && brace_depth == 0) {
            p++;
            break;
        }
        if (false_len < false_cap - 1) false_branch[false_len++] = *p++;
    }
    false_branch[false_len] = '\0';
    
    *fmt = p;
    return true;
}

/* Parse a window list loop: #{W:format} */
static bool format_parse_window_loop(const char **fmt, char *loop_fmt, size_t loop_cap) {
    const char *p = *fmt;
    
    size_t fmt_len = 0;
    int brace_depth = 0;
    while (*p) {
        if (*p == '#' && *(p + 1) == '{') {
            brace_depth++;
            if (fmt_len < loop_cap - 1) loop_fmt[fmt_len++] = *p++;
            if (fmt_len < loop_cap - 1) loop_fmt[fmt_len++] = *p++;
            continue;
        }
        if (*p == '}' && brace_depth > 0) {
            brace_depth--;
            if (fmt_len < loop_cap - 1) loop_fmt[fmt_len++] = *p++;
            continue;
        }
        if (*p == '}' && brace_depth == 0) {
            p++;
            break;
        }
        if (fmt_len < loop_cap - 1) loop_fmt[fmt_len++] = *p++;
    }
    loop_fmt[fmt_len] = '\0';
    
    *fmt = p;
    return true;
}