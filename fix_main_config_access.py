#!/usr/bin/env python3
with open('src/cli/main.c', 'r') as f:
    content = f.read()

old_text = '''/* Initialize logging from config */
        if (app->config) {
            lmux_log_open_file(app->config->log_file, app->config->log_max_size_mb,
                          app->config->log_max_files, app->config->log_level, app->config->log_json);
            lmux_log_set_level(app->config->log_level);
        }'''

new_text = '''/* Initialize logging from config */
        const lmux_config *cfg = lmux_app_config(app);
        if (cfg) {
            lmux_log_open_file(cfg->log_file, cfg->log_max_size_mb,
                          cfg->log_max_files, cfg->log_level, cfg->log_json);
            lmux_log_set_level(cfg->log_level);
        }'''

with open('src/cli/main.c', 'r') as f:
    content = f.read()

if old_text in content:
    content = content.replace(old_text, new_text)
    with open('src/cli/main.c', 'w') as f:
        f.write(content)
    print("Fixed config access")
else:
    print("Text not found!")