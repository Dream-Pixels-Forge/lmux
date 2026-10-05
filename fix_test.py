#!/usr/bin/env python3
with open('tests/test_updater.py', 'r') as f:
    content = f.read()

# Replace the broken imports
new_content = content.replace(
    'from updater import (\n    CURRENT_VERSION,\n    _parse_version,\n    compare_versions,\n    check_for_updates,\n    GitHubUpdater,\n    UpdateChecker,\n    add_update_notification,\n)',
    'from updater import (\n    CURRENT_VERSION,\n    _parse_version,\n    compare_versions,\n    SparkleUpdater,\n    UpdateChecker,\n    UpdateInfo,\n    AppCastFeed,\n    add_update_notification,\n)'
)

# Replace GitHubUpdater references
new_content = new_content.replace('GitHubUpdater', 'SparkleUpdater')

with open('tests/test_updater.py', 'w') as f:
    f.write(new_content)

print("Fixed imports")