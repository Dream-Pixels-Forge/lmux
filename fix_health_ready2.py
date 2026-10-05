#!/usr/bin/env python3
with open('src/core/model.c', 'r') as f:
    content = f.read()

old_text = '''        bool ready = app->running
                     && app->listen_fd >= 0
                     && !daemon_should_exit
                     && rwlock_free
                     && app->workspaces.len > 0;'''

new_text = '''        bool ready = app->running
                     && app->listen_fd >= 0
                     && !lmux_daemon_should_exit()
                     && rwlock_free
                     && app->workspaces.len > 0;'''

if old_text in content:
    content = content.replace(old_text, new_text)
    with open('src/core/model.c', 'w') as f:
        f.write(content)
    print("Fixed daemon_should_exit reference")
else:
    print("Text not found!")