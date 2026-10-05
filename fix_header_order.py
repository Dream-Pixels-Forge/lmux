#!/usr/bin/env python3
with open('include/lmux.h', 'r') as f:
    content = f.read()

# Remove the premature declaration
old_text = '''/* Get app config (for CLI access to config) */
const lmux_config *lmux_app_config(const lmux_app *app);'''

new_text = ''''''

if old_text in content:
    content = content.replace(old_text, new_text)
    with open('include/lmux.h', 'w') as f:
        f.write(content)
    print("Removed premature declaration")
else:
    print("Text not found!")