#!/usr/bin/env python3
with open('src/core/model.c', 'r') as f:
    content = f.read()

old_text = '''        else if (daemon_should_exit) reason = "shutting_down";'''

new_text = '''        else if (lmux_daemon_should_exit()) reason = "shutting_down";'''

with open('src/core/model.c', 'r') as f:
    content = f.read()

if old_text in content:
    content = content.replace(old_text, new_text)
    with open('src/core/model.c', 'w') as f:
        f.write(content)
    print("Fixed daemon_should_exit reference in reason")
else:
    print("Text not found!")