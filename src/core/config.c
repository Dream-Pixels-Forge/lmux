/*
 * config.c - Configuration file support for lmux.
 *
 * Reads/writes a JSON config file at $XDG_CONFIG_HOME/lmux/config.json
 * or ~/.config/lmux/config.json. Provides defaults for all settings.
 * Uses the same json_extract_string pattern from model.c.
 */
#define _POSIX_C_SOURCE 200809L

#include "lmux.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <unistd.h>
#include <sys/stat.h>
#include <errno.h>

/* ----- Minimal JSON extraction (same pattern as model.c) ----- */

/* Escape a string for JSON output. Writes to out buffer, returns out.
 * Handles: ", \, \n, \r, \t, and control chars < 0x20 with \uXXXX encoding. */
static char *cfg_json_escape(const char *in, char *out, size_t cap) {
    if (!in || !out || cap == 0) { if (out && cap > 0) out[0] = 0; return out; }
    size_t oi = 0;
    for (const char *p = in; *p && oi + 6 < cap; p++) {
        unsigned char c = (unsigned char)*p;
        switch (c) {
        case '"':  out[oi++] = '\\'; out[oi++] = '"'; break;
        case '\\': out[oi++] = '\\'; out[oi++] = '\\'; break;
        case '\n': out[oi++] = '\\'; out[oi++] = 'n'; break;
        case '\r': out[oi++] = '\\'; out[oi++] = 'r'; break;
        case '\t': out[oi++] = '\\'; out[oi++] = 't'; break;
        case '\b': out[oi++] = '\\'; out[oi++] = 'b'; break;
        case '\f': out[oi++] = '\\'; out[oi++] = 'f'; break;
        default:
            if (c < 0x20) {
                oi += (size_t)snprintf(out + oi, cap - oi, "\\u%04x", c);
            } else {
                out[oi++] = c;
            }
        }
    }
    out[oi] = 0;
    return out;
}

static const char *cfg_json_find_key(const char *json, const char *key) {
    if (!json || !key) return NULL;
    char needle[256];
    snprintf(needle, sizeof needle, "\"%s\"", key);
    const char *p = strstr(json, needle);
    if (!p) return NULL;
    const char *colon = strchr(p + strlen(needle), ':');
    if (!colon) return NULL;
    const char *val = colon + 1;
    while (*val == ' ' || *val == '\t' || *val == '\n' || *val == '\r') val++;
    return val;
}

static bool cfg_json_extract_string(const char *json, const char *key,
                                     char *out, size_t cap) {
    const char *val = cfg_json_find_key(json, key);
    if (!val) return false;
    if (*val != '"') return false;
    val++;
    size_t i = 0;
    while (*val && *val != '"' && i < cap - 1) {
        if (*val == '\\' && *(val + 1)) {
            char next = *(val + 1);
            if (next == 'n') { out[i++] = '\n'; val += 2; }
            else if (next == 't') { out[i++] = '\t'; val += 2; }
            else if (next == '\\') { out[i++] = '\\'; val += 2; }
            else if (next == '"') { out[i++] = '"'; val += 2; }
            else { out[i++] = *val++; }
        } else {
            out[i++] = *val++;
        }
    }
    out[i] = 0;
    return true;
}

static bool cfg_json_extract_int(const char *json, const char *key, int *out) {
    const char *val = cfg_json_find_key(json, key);
    if (!val) return false;
    /* Reject non-numeric values (type mismatch) */
    if (*val != '-' && (*val < '0' || *val > '9')) return false;
    *out = (int)strtol(val, NULL, 10);
    return true;
}

static bool cfg_json_extract_bool(const char *json, const char *key, bool *out) {
    const char *val = cfg_json_find_key(json, key);
    if (!val) return false;
    if (strncmp(val, "true", 4) == 0) { *out = true; return true; }
    if (strncmp(val, "false", 5) == 0) { *out = false; return true; }
    return false;
}

/* ----- Config defaults ----- */

lmux_config *lmux_config_new(void) {
    lmux_config *c = calloc(1, sizeof *c);
    snprintf(c->font_family, sizeof c->font_family, "monospace");
    c->font_size = 12;
    snprintf(c->theme, sizeof c->theme, "dark");
    c->scrollback_lines = 10000;
    c->show_sidebar = true;
    c->show_notifications_panel = true;
    c->auto_save_session = true;
    c->auto_save_interval_sec = 30;
    snprintf(c->default_shell, sizeof c->default_shell, "/bin/bash");
    /* Agent paths — empty means use PATH */
    c->keybindings = (lmux_config_vec){0};
    c->themes = (lmux_config_vec){0};
    c->workspace_groups = (lmux_config_vec){0};
    return c;
}

void lmux_config_free(lmux_config *cfg) {
    if (!cfg) return;
    for (size_t i = 0; i < cfg->keybindings.len; i++)
        free(cfg->keybindings.items[i]);
    free(cfg->keybindings.items);
    for (size_t i = 0; i < cfg->themes.len; i++)
        free(cfg->themes.items[i]);
    free(cfg->themes.items);
    for (size_t i = 0; i < cfg->workspace_groups.len; i++) {
        lmux_workspace_group *g = cfg->workspace_groups.items[i];
        if (g) {
            for (size_t j = 0; j < g->workspace_ids.len; j++)
                free(g->workspace_ids.items[j]);
            free(g->workspace_ids.items);
            free(g);
        }
    }
    free(cfg->workspace_groups.items);
    free(cfg);
}

/* ----- Vector helpers (local) ----- */

static bool cfg_vec_push(lmux_config_vec *v, void *item) {
    if (v->len == v->cap) {
        v->cap = v->cap ? v->cap * 2 : 8;
        void *new_items = realloc(v->items, v->cap * sizeof(void *));
        if (!new_items) return false;
        v->items = new_items;
    }
    v->items[v->len++] = item;
    return true;
}

/* ----- Config I/O ----- */

const char *lmux_config_path(char *buf, size_t cap) {
    const char *xdg = getenv("XDG_CONFIG_HOME");
    if (xdg && xdg[0]) {
        snprintf(buf, cap, "%s/lmux/config.json", xdg);
    } else {
        const char *home = getenv("HOME");
        snprintf(buf, cap, "%s/.config/lmux/config.json", home ? home : "/tmp");
    }
    return buf;
}

/* Validate basic JSON structure: balanced braces, proper brackets. */
static bool cfg_json_valid(const char *json, size_t len) {
    /* Must start with '{' */
    const char *p = json;
    while (*p && (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')) p++;
    if (*p != '{') return false;
    /* Brace/bracket balance */
    int braces = 0, brackets = 0;
    bool in_string = false;
    for (const char *c = json; c < json + len && *c; c++) {
        if (in_string) {
            if (*c == '"' && (c == json || *(c-1) != '\\')) in_string = false;
            continue;
        }
        if (*c == '"') { in_string = true; continue; }
        if (*c == '{') braces++;
        else if (*c == '}') braces--;
        else if (*c == '[') brackets++;
        else if (*c == ']') brackets--;
        if (braces < 0 || brackets < 0) return false; /* extra closing */
    }
    return braces == 0 && brackets == 0;
}

/* Load config from a pre-read buffer (avoids duplicate file I/O). */
bool lmux_config_load_buf(lmux_config *cfg, const char *buf, size_t len) {
    if (!cfg || !buf || len == 0) return false;
    /* Validate JSON structure before parsing */
    if (!cfg_json_valid(buf, len)) {
        lmux_log(LMUX_LOG_WARN, "config: malformed JSON, using defaults");
        return false;
    }

    cfg_json_extract_string(buf, "font_family", cfg->font_family, sizeof cfg->font_family);
    cfg_json_extract_int(buf, "font_size", &cfg->font_size);
    cfg_json_extract_string(buf, "theme", cfg->theme, sizeof cfg->theme);
    cfg_json_extract_int(buf, "scrollback_lines", &cfg->scrollback_lines);
    cfg_json_extract_bool(buf, "show_sidebar", &cfg->show_sidebar);
    cfg_json_extract_bool(buf, "show_notifications_panel", &cfg->show_notifications_panel);
    cfg_json_extract_bool(buf, "auto_save_session", &cfg->auto_save_session);
    cfg_json_extract_int(buf, "auto_save_interval_sec", &cfg->auto_save_interval_sec);
    cfg_json_extract_string(buf, "default_shell", cfg->default_shell, sizeof cfg->default_shell);

    /* Agent paths */
    cfg_json_extract_string(buf, "agent_claude_code", cfg->agent_paths[0], 512);
    cfg_json_extract_string(buf, "agent_opencode", cfg->agent_paths[1], 512);
    cfg_json_extract_string(buf, "agent_codex", cfg->agent_paths[2], 512);
    cfg_json_extract_string(buf, "agent_aider", cfg->agent_paths[3], 512);
    cfg_json_extract_string(buf, "agent_goose", cfg->agent_paths[4], 512);

    /* Parse keybindings */
    {
        const char *kb_start = cfg_json_find_key(buf, "keybindings");
        if (kb_start && *kb_start == '{') {
            const char *p = kb_start + 1;
            while (*p && *p != '}') {
                while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') p++;
                if (*p != '"') break;
                p++;
                char key[64] = {0};
                size_t ki = 0;
                while (*p && *p != '"' && ki < sizeof key - 1) {
                    if (*p == '\\' && *(p+1)) { p++; }
                    key[ki++] = *p++;
                }
                if (*p == '"') p++;
                while (*p == ' ' || *p == ':' || *p == '\t') p++;
                if (*p != '"') break;
                p++;
                char action[128] = {0};
                size_t ai = 0;
                while (*p && *p != '"' && ai < sizeof action - 1) {
                    if (*p == '\\' && *(p+1)) { p++; }
                    action[ai++] = *p++;
                }
                if (*p == '"') p++;
                lmux_keybinding *kb = calloc(1, sizeof *kb);
                snprintf(kb->key, sizeof kb->key, "%s", key);
                snprintf(kb->action, sizeof kb->action, "%s", action);
                cfg_vec_push(&cfg->keybindings, kb);
                while (*p == ' ' || *p == ',' || *p == '\n' || *p == '\r' || *p == '\t') p++;
            }
        }
    }

    /* Parse workspace groups */
    {
        const char *gr_start = cfg_json_find_key(buf, "groups");
        if (gr_start && *gr_start == '{') {
            const char *p = gr_start + 1;
            while (*p && *p != '}') {
                while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') p++;
                if (*p != '"') break;
                p++;
                char gname[64] = {0};
                size_t gi = 0;
                while (*p && *p != '"' && gi < sizeof gname - 1) {
                    if (*p == '\\' && *(p+1)) { p++; }
                    gname[gi++] = *p++;
                }
                if (*p == '"') p++;
                while (*p == ' ' || *p == ':' || *p == '\t') p++;
                if (*p != '[') break;
                p++;
                lmux_workspace_group *g = lmux_workspace_group_create(cfg, gname);
                while (*p && *p != ']') {
                    while (*p == ' ' || *p == ',' || *p == '\n' || *p == '\t') p++;
                    if (*p == '"') {
                        p++;
                        char wid_str[64] = {0};
                        size_t wi = 0;
                        while (*p && *p != '"' && wi < sizeof wid_str - 1) {
                            wid_str[wi++] = *p++;
                        }
                        if (*p == '"') p++;
                        lmux_id wid = (lmux_id)atol(wid_str);
                        if (wid > 0) {
                            lmux_id *idp = malloc(sizeof(lmux_id));
                            *idp = wid;
                            cfg_vec_push(&g->workspace_ids, idp);
                        }
                    } else {
                        break;
                    }
                }
                if (*p == ']') p++;
                while (*p == ' ' || *p == ',' || *p == '\n' || *p == '\r' || *p == '\t') p++;
            }
        }
    }

    return true;
}

bool lmux_config_load(lmux_config *cfg, const char *path) {
    if (!cfg || !path) return false;
    FILE *f = fopen(path, "r");
    if (!f) return false; /* Not an error — simply no config yet */
    char buf[32768];
    size_t n = fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    if (n == 0) return false;
    buf[n] = 0;

    /* Delegate to the buffer-based loader to avoid code duplication */
    return lmux_config_load_buf(cfg, buf, n);
}

bool lmux_config_save(const lmux_config *cfg, const char *path) {
    if (!cfg || !path) return false;
    /* Ensure directory exists */
    char dir[1024];
    snprintf(dir, sizeof dir, "%s", path);
    char *slash = strrchr(dir, '/');
    if (slash) { *slash = 0; mkdir(dir, 0755); }

    /* Write to temp, then rename (atomic) */
    char tmp[1024];
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    FILE *f = fopen(tmp, "w");
    if (!f) return false;

    /* Escape buffers for strings that may contain special characters */
    char esc_buf[2048];

    fprintf(f, "{\n");
    fprintf(f, "  \"font_family\": \"%s\",\n", cfg_json_escape(cfg->font_family, esc_buf, sizeof esc_buf));
    fprintf(f, "  \"font_size\": %d,\n", cfg->font_size);
    fprintf(f, "  \"theme\": \"%s\",\n", cfg_json_escape(cfg->theme, esc_buf, sizeof esc_buf));
    fprintf(f, "  \"scrollback_lines\": %d,\n", cfg->scrollback_lines);
    fprintf(f, "  \"show_sidebar\": %s,\n", cfg->show_sidebar ? "true" : "false");
    fprintf(f, "  \"show_notifications_panel\": %s,\n", cfg->show_notifications_panel ? "true" : "false");
    fprintf(f, "  \"auto_save_session\": %s,\n", cfg->auto_save_session ? "true" : "false");
    fprintf(f, "  \"auto_save_interval_sec\": %d,\n", cfg->auto_save_interval_sec);
    fprintf(f, "  \"default_shell\": \"%s\",\n", cfg_json_escape(cfg->default_shell, esc_buf, sizeof esc_buf));
    fprintf(f, "  \"agent_claude_code\": \"%s\",\n", cfg_json_escape(cfg->agent_paths[0], esc_buf, sizeof esc_buf));
    fprintf(f, "  \"agent_opencode\": \"%s\",\n", cfg_json_escape(cfg->agent_paths[1], esc_buf, sizeof esc_buf));
    fprintf(f, "  \"agent_codex\": \"%s\",\n", cfg_json_escape(cfg->agent_paths[2], esc_buf, sizeof esc_buf));
    fprintf(f, "  \"agent_aider\": \"%s\",\n", cfg_json_escape(cfg->agent_paths[3], esc_buf, sizeof esc_buf));
    fprintf(f, "  \"agent_goose\": \"%s\",\n", cfg_json_escape(cfg->agent_paths[4], esc_buf, sizeof esc_buf));

    /* Keybindings */
    fprintf(f, "  \"keybindings\": [\n");
    for (size_t i = 0; i < cfg->keybindings.len; i++) {
        lmux_keybinding *kb = cfg->keybindings.items[i];
        fprintf(f, "    {\"key\": \"%s\", \"action\": \"%s\"}%s\n",
                cfg_json_escape(kb->key, esc_buf, sizeof esc_buf),
                cfg_json_escape(kb->action, esc_buf, sizeof esc_buf),
                i + 1 < cfg->keybindings.len ? "," : "");
    }
    fprintf(f, "  ],\n");

    /* Themes */
    fprintf(f, "  \"themes\": [\n");
    for (size_t i = 0; i < cfg->themes.len; i++) {
        lmux_theme_entry *t = cfg->themes.items[i];
        fprintf(f, "    {\"name\": \"%s\", \"fg\": \"%s\", \"bg\": \"%s\", \"cursor\": \"%s\"}%s\n",
                cfg_json_escape(t->name, esc_buf, sizeof esc_buf),
                cfg_json_escape(t->fg, esc_buf, sizeof esc_buf),
                cfg_json_escape(t->bg, esc_buf, sizeof esc_buf),
                cfg_json_escape(t->cursor, esc_buf, sizeof esc_buf),
                i + 1 < cfg->themes.len ? "," : "");
    }
    fprintf(f, "  ],\n");

    /* Workspace groups */
    fprintf(f, "  \"workspace_groups\": [\n");
    for (size_t i = 0; i < cfg->workspace_groups.len; i++) {
        lmux_workspace_group *g = cfg->workspace_groups.items[i];
        fprintf(f, "    {\"name\": \"%s\", \"workspace_ids\": [",
                cfg_json_escape(g->name, esc_buf, sizeof esc_buf));
        for (size_t j = 0; j < g->workspace_ids.len; j++) {
            if (j > 0) fprintf(f, ", ");
            fprintf(f, "%u", *(lmux_id *)g->workspace_ids.items[j]);
        }
        fprintf(f, "]}%s\n", i + 1 < cfg->workspace_groups.len ? "," : "");
    }
    fprintf(f, "  ]\n");

    fprintf(f, "}\n");
    fclose(f);

    if (rename(tmp, path) != 0) {
        unlink(tmp);
        return false;
    }
    return true;
}

lmux_keybinding *lmux_config_find_key(lmux_config *cfg, const char *key) {
    if (!cfg || !key) return NULL;
    for (size_t i = 0; i < cfg->keybindings.len; i++) {
        lmux_keybinding *kb = cfg->keybindings.items[i];
        if (strcmp(kb->key, key) == 0) return kb;
    }
    return NULL;
}

lmux_theme_entry *lmux_config_find_theme(lmux_config *cfg, const char *name) {
    if (!cfg || !name) return NULL;
    for (size_t i = 0; i < cfg->themes.len; i++) {
        lmux_theme_entry *t = cfg->themes.items[i];
        if (strcmp(t->name, name) == 0) return t;
    }
    return NULL;
}

lmux_workspace_group *lmux_workspace_group_find(lmux_config *cfg, const char *name) {
    if (!cfg || !name) return NULL;
    for (size_t i = 0; i < cfg->workspace_groups.len; i++) {
        lmux_workspace_group *g = cfg->workspace_groups.items[i];
        if (strcmp(g->name, name) == 0) return g;
    }
    return NULL;
}

lmux_workspace_group *lmux_workspace_group_create(lmux_config *cfg, const char *name) {
    if (!cfg || !name) return NULL;
    /* Don't create duplicates */
    lmux_workspace_group *existing = lmux_workspace_group_find(cfg, name);
    if (existing) return existing;
    lmux_workspace_group *g = calloc(1, sizeof *g);
    snprintf(g->name, sizeof g->name, "%s", name);
    g->workspace_ids = (lmux_config_vec){0};
    cfg_vec_push(&cfg->workspace_groups, g);
    return g;
}

bool lmux_workspace_group_add(lmux_config *cfg, const char *name, lmux_id ws_id) {
    lmux_workspace_group *g = lmux_workspace_group_find(cfg, name);
    if (!g) return false;
    lmux_id *idp = malloc(sizeof(lmux_id));
    *idp = ws_id;
    cfg_vec_push(&g->workspace_ids, idp);
    return true;
}

bool lmux_workspace_group_remove(lmux_config *cfg, const char *name, lmux_id ws_id) {
    lmux_workspace_group *g = lmux_workspace_group_find(cfg, name);
    if (!g) return false;
    for (size_t i = 0; i < g->workspace_ids.len; i++) {
        lmux_id *idp = g->workspace_ids.items[i];
        if (*idp == ws_id) {
            free(idp);
            memmove(&g->workspace_ids.items[i], &g->workspace_ids.items[i + 1],
                    (g->workspace_ids.len - i - 1) * sizeof(void *));
            g->workspace_ids.len--;
            return true;
        }
    }
    return false;
}
