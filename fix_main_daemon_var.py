#!/usr/bin/env python3
with open('src/cli/main.c', 'r') as f:
    content = f.read()

old_text = '''static volatile sig_atomic_t daemon_should_exit = 0;
static lmux_app *daemon_app_global = NULL;'''

new_text = '''extern volatile sig_atomic_t daemon_should_exit;
static lmux_app *daemon_app_global = NULL;'''

with open('src/cli/main.c', 'r') as f:
    content = f.read()

if old_text in content:
    content = content.replace(old_text, new_text)
    with open('src/cli/main.c', 'w') as f:
        f.write(content)
    print("Changed daemon_should_exit to extern in main.c")
else:
    print("Text not found!")