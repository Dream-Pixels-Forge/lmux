#!/usr/bin/env python3
with open('src/core/model.c', 'r') as f:
    content = f.read()

# Find the lmux_log_request function
start = content.find('void lmux_log_request')
end = content.find('void lmux_app_quit')

if start >= 0 and end >= 0:
    old_func = content[start:end]
    print("Found function at", start, "to", end)
    
    new_func = '''/**
 * Log a request/response with a correlation ID for distributed tracing.
 *
 * @param request_id  Unique ID for this request (e.g., "req-12345").
 * @param cmd         The command name (e.g., "workspace.create").
 * @param ok          Whether the request succeeded.
 * @param detail      Optional detail message (can be NULL).
 */
void lmux_log_request(const char *request_id, const char *cmd, bool ok, const char *detail) {
    if (g_log_level > LMUX_LOG_INFO && g_log_level_config > LMUX_LOG_INFO) return;

    if (log_json_enabled()) {
        time_t now = time(NULL);
        struct tm tm_buf;
        struct tm *tm = localtime_r(&now, &tm_buf);
        char timestamp[32];
        strftime(timestamp, sizeof timestamp, "%Y-%m-%dT%H:%M:%S", tm);

        char cmd_escaped[256], detail_escaped[512];
        log_json_escape(cmd ? cmd : "", cmd_escaped, sizeof cmd_escaped);
        log_json_escape(detail ? detail : "", detail_escaped, sizeof detail_escaped);

        char json_line[4096];
        snprintf(json_line, sizeof json_line,
            "{\\\"timestamp\\\":\\\"%s\\\",\\\"level\\\":\\\"info\\\",\\\"service\\\":\\\"lmux\\\","
            "\\\"trace_id\\\":\\\"%s\\\",\\\"cmd\\\":\\\"%s\\\",\\\"ok\\\":%s}",
            timestamp, request_id ? request_id : "null", cmd_escaped,
            ok ? "true" : "false");
        if (detail && detail[0]) {
            strncat(json_line, ",\\\"detail\\\":\\\"", sizeof json_line - strlen(json_line) - 1);
            strncat(json_line, detail_escaped, sizeof json_line - strlen(json_line) - 1);
            strncat(json_line, "\\\"", sizeof json_line - strlen(json_line) - 1);
        }
        strncat(json_line, "}", sizeof json_line - strlen(json_line) - 1);

        /* Write to stderr (systemd journal) */
        fprintf(stderr, "%s\\n", json_line);

        /* Write to log file if configured */
        if (g_log_file_fp) {
            fprintf(g_log_file_fp, "%s\\n", json_line);
            fflush(g_log_file_fp);
        }
    } else {
        /* Legacy format */
        fprintf(stderr, "[lmux INFO ] %s: %s %s\\n",
                request_id ? request_id : "-", cmd ? cmd : "-",
                ok ? "OK" : "FAIL");

        /* Write to log file if configured */
        if (g_log_file_fp) {
            fprintf(g_log_file_fp, "[lmux INFO ] %s: %s %s\\n",
                    request_id ? request_id : "-", cmd ? cmd : "-",
                    ok ? "OK" : "FAIL");
            fflush(g_log_file_fp);
        }
    }
}
'''
    
    new_content = content[:start] + new_func + content[end:]
    with open('src/core/model.c', 'w') as f:
        f.write(new_content)
    print("Replaced function")
else:
    print("Not found")