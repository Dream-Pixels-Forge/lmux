#!/usr/bin/env python3
with open('include/lmux.h', 'r') as f:
    content = f.read()

old_text = '''void lmux_log_set_level(lmux_log_level level);
void lmux_log(lmux_log_level level, const char *fmt, ...)
    __attribute__((format(printf, 2, 3)));
void lmux_log_request(const char *request_id, const char *cmd, bool ok, const char *detail);'''

new_text = '''void lmux_log_set_level(lmux_log_level level);
void lmux_log(lmux_log_level level, const char *fmt, ...)
    __attribute__((format(printf, 2, 3)));
void lmux_log_request(const char *request_id, const char *cmd, bool ok, const char *detail);

/* Log file management (open/close/rotate) */
void lmux_log_open_file(const char *path, int max_size_mb, int max_files, int log_level, bool log_json);'''

if old_text in content:
    content = content.replace(old_text, new_text)
    with open('include/lmux.h', 'w') as f:
        f.write(content)
    print("Added log_open_file declaration")
else:
    print("Text not found!")