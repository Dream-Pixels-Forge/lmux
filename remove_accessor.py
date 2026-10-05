#!/usr/bin/env python3
with open('src/cli/main.c', 'r') as f:
    content = f.read()

old_text = '''static volatile sig_atomic_t daemon_should_exit = 0;
static lmux_app *daemon_app_global = NULL;

/* Accessor for daemon_should_exit (used by health.ready in model.c) */
bool lmux_daemon_should_exit(void) {
    return daemon_should_exit;
}

/* Async-signal-safe SIGCHLD handler: reap terminated children immediately. */'''

new_text = '''static volatile sig_atomic_t daemon_should_exit = 0;
static lmux_app *daemon_app_global = NULL;

/* Async-signal-safe SIGCHLD handler: reap terminated children immediately. */'''

if old_text in content:
    content = content.replace(old_text, new_text)
    with open('src/cli/main.c', 'w') as f:
        f.write(content)
    print("Removed accessor from main.c")
else:
    print("Text not found!")