#!/usr/bin/env python3
with open('src/cli/main.c', 'r') as f:
    content = f.read()

old_text = '''printf("  health.live                        Check if daemon is alive\\n");
    printf("  health.ready                       Check if daemon is ready\\n");
    printf("  metrics                            Show server metrics\\n\\n");'''

new_text = '''printf("  health.live                        Check if daemon is alive\\n");
    printf("  health.ready                       Check if daemon is ready\\n");
    printf("  metrics                            Show server metrics\\n");
    printf("  logs [--follow] [--level <level>]  View daemon logs (follow like tail -f)\\n\\n");'''

if old_text in content:
    content = content.replace(old_text, new_text)
    with open('src/cli/main.c', 'w') as f:
        f.write(content)
    print("Added logs command to help text")
else:
    print("Text not found!")