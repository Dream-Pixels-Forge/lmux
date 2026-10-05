#!/usr/bin/env python3
with open('src/core/model.c', 'r') as f:
    content = f.read()

# Add the global variable and accessor after the log file state variables
old_text = '''/* Check if JSON logging is enabled (env var or config) */
static int log_json_enabled(void) {'''

new_text = '''/* Daemon shutdown flag (shared with main.c via extern) */
volatile sig_atomic_t daemon_should_exit = 0;

/* Accessor for daemon_should_exit (used by health.ready) */
bool lmux_daemon_should_exit(void) {
    return daemon_should_exit;
}

/* Check if JSON logging is enabled (env var or config) */
static int log_json_enabled(void) {'''

if old_text in content:
    content = content.replace(old_text, new_text)
    with open('src/core/model.c', 'w') as f:
        f.write(content)
    print("Added daemon_should_exit variable and accessor to model.c")
else:
    print("Text not found!")