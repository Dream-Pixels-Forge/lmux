#!/usr/bin/env python3
with open('src/core/model.c', 'r') as f:
    content = f.read()

old_text = '''    /* --- health.ready --- All dependencies healthy. */
    if (strcmp(cmd, "health.ready") == 0) {
        /* Check: app running, workspaces accessible, server listening. */
        bool ready = app->running && app->listen_fd >= 0;
        written = snprintf(result, result_cap,
            "{\\\"ok\\\":%s,\\\"result\\\":{\\\"status\\\":\\\"%s\\\",\\\"workspaces\\\":%zu,\\\"surfaces\\\":%zu}}",
            ready ? "true" : "false",
            ready ? "ready" : "not_ready",
            app->workspaces.len,
            (size_t)0);  /* surface count summed below if needed */
        goto done;
    }'''

new_text = '''    /* --- health.ready --- All dependencies healthy. */
    if (strcmp(cmd, "health.ready") == 0) {
        /* Check: app running, workspaces accessible, server listening,
         * model not locked (rwlock), not shutting down. */
        bool rwlock_free = pthread_rwlock_tryrdlock(&app->rw_lock) == 0;
        if (rwlock_free) pthread_rwlock_unlock(&app->rw_lock);

        bool ready = app->running
                     && app->listen_fd >= 0
                     && !daemon_should_exit
                     && rwlock_free
                     && app->workspaces.len > 0;

        /* Sum surfaces across all workspaces */
        size_t total_surfaces = 0;
        for (size_t i = 0; i < app->workspaces.len; i++) {
            lmux_workspace *ws = app->workspaces.items[i];
            total_surfaces += ws->surfaces.len;
        }

        const char *reason = "ready";
        if (!app->running) reason = "app_not_running";
        else if (app->listen_fd < 0) reason = "server_not_listening";
        else if (daemon_should_exit) reason = "shutting_down";
        else if (!rwlock_free) reason = "model_locked";
        else if (app->workspaces.len == 0) reason = "no_workspaces";

        written = snprintf(result, result_cap,
            "{\\\"ok\\\":%s,\\\"result\\\":{\\\"status\\\":\\\"%s\\\",\\\"reason\\\":\\\"%s\\\","
            "\\\"workspaces\\\":%zu,\\\"surfaces\\\":%zu,\\\"uptime_seconds\\\":%ld}}",
            ready ? "true" : "false",
            ready ? "ready" : "not_ready",
            reason,
            app->workspaces.len,
            total_surfaces,
            (long)(time(NULL) - app->start_time));
        goto done;
    }'''

if old_text in content:
    content = content.replace(old_text, new_text)
    with open('src/core/model.c', 'w') as f:
        f.write(content)
    print("Replaced health.ready endpoint")
else:
    print("Text not found!")