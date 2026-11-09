/**
 * lmux_json.h — Minimal, bounds-safe JSON field helpers (internal).
 *
 * The daemon does not build a DOM. Each request is a single line of JSON and
 * handlers pull individual fields out of it on demand. These helpers are the
 * only place that does that scanning, so the rules live in one file:
 *
 *   - Keys are matched only where a JSON *key* can appear: a complete string
 *     token, outside any other string, followed by ':'. Text inside a value
 *     (e.g. "text":"\"pane\":\"3\"") can never be mistaken for a key.
 *   - Output buffers are never written past `cap` (including the NUL), no
 *     matter how the input ends or which escapes it contains.
 *   - \uXXXX escapes decode to UTF-8, including UTF-16 surrogate pairs.
 *     Invalid or lone surrogates decode to U+FFFD. \u0000 is dropped so the
 *     result stays a well-formed C string.
 *
 * Inputs are NUL-terminated and may be malformed; every function degrades to
 * "not found" / partial output rather than reading past the terminator.
 */
#ifndef LMUX_JSON_H
#define LMUX_JSON_H

#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Pointer to the first non-whitespace character of the value of the first
 * `key` at any nesting depth, or NULL. */
const char *lmux_json_find_key(const char *json, const char *key);

/* Like lmux_json_find_key but only matches keys of the outermost object
 * (depth 1 relative to `json`). `depth_out` may be NULL. */
const char *lmux_json_find_toplevel_key(const char *json, const char *key,
                                        int *depth_out);

/* Decode the JSON string starting at `v` (which must point at the opening
 * quote) into `out`. Returns false if `v` is not a string or cap == 0. */
bool lmux_json_decode_string(const char *v, char *out, size_t cap);

/* Field extractors (any depth). Return false when the key is missing or the
 * value has the wrong type; `out` is left untouched in that case. */
bool lmux_json_extract_string(const char *json, const char *key,
                              char *out, size_t cap);
bool lmux_json_extract_int(const char *json, const char *key, int *out);
bool lmux_json_extract_bool(const char *json, const char *key, bool *out);

/* Escape `in` for use inside a JSON string literal (no surrounding quotes).
 * Truncates cleanly (never mid-escape) when `cap` is too small. */
char *lmux_json_escape(char *out, size_t cap, const char *in);

/* Escape into a thread-local rotating buffer (8 slots x 2048 bytes), so up to
 * eight results can be used in one printf-style call. Intended for event and
 * log payloads; longer values are truncated. Never returns NULL. */
const char *lmux_json_esc(const char *in);

#ifdef __cplusplus
}
#endif

#endif /* LMUX_JSON_H */
