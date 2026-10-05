#!/usr/bin/env python3
with open('src/core/model.c', 'r') as f:
    content = f.read()

old_text = '''/* --- logs --- View daemon logs */
    if (strcmp(cmd, "logs") == 0) {
        bool follow = false;
        const char *level = NULL;
        json_extract_bool(args_json, "follow", &follow);
        json_extract_string(args_json, "level", (char *)level, 64);

        /* For now, just return recent log entries from the log file if configured */
        /* In a full implementation, this would stream logs if follow=true */
        const char *log_path = app->config ? app->config->log_file : "";'''

new_text = '''/* --- logs --- View daemon logs */
    if (strcmp(cmd, "logs") == 0) {
        bool follow = false;
        char level[64] = {0};
        json_extract_bool(args_json, "follow", &follow);
        json_extract_string(args_json, "level", level, sizeof level);

        /* For now, just return recent log entries from the log file if configured */
        /* In a full implementation, this would stream logs if follow=true */
        const char *log_path = app->config ? app->config->log_file : "";'''

if old_text in content:
    content = content.replace(old_text, new_text)
    with open('src/core/model.c', 'w') as f:
        f.write(content)
    print("Fixed level variable")
else:
    print("Text not found!")