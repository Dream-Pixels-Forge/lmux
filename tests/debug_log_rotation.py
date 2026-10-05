#!/usr/bin/env python3
"""Debug script to verify log rotation and max_files limit."""

import tempfile
import os
import json
import time
import shutil
from pathlib import Path
import sys
sys.path.insert(0, 'tests')
from lmux import LmuxClient, LmuxDaemon

home = tempfile.mkdtemp(prefix='lmux-logrot-home-')
home_path = Path(home)
data = home_path / '.local' / 'share'
(data / 'lmux').mkdir(parents=True, exist_ok=True)
config_dir = home_path / '.config'
config_dir.mkdir(parents=True, exist_ok=True)

log_file = str(home_path / 'logs' / 'lmux.log')
(home_path / 'logs').mkdir(parents=True, exist_ok=True)
config = {
    'log_level': 0,
    'log_file': log_file,
    'log_max_size_mb': 1,
    'log_max_files': 3,
    'log_json': False,
}
config_path = config_dir / 'lmux' / 'config.json'
config_path.parent.mkdir(parents=True, exist_ok=True)
config_path.write_text(json.dumps(config, indent=2))

env = dict(os.environ)
env['HOME'] = str(home_path)
env['XDG_DATA_HOME'] = str(data)
env['XDG_CONFIG_HOME'] = str(config_dir)
sock = f'/tmp/lmux-logrot-{os.getpid()}.sock'
Path(sock).unlink(missing_ok=True)
daemon = LmuxDaemon(sock, env=env)
client = daemon.start(timeout=60)

try:
    # Trigger 5 rotations to test max_files limit (should keep only 3 rotated + 1 main = 4)
    for rotation in range(5):
        print(f"\n--- Rotation {rotation + 1} ---")
        
        # Pad the log file to exceed 1 MB
        log_path = Path(log_file)
        if log_path.exists():
            current_size = log_path.stat().st_size
            target_size = 1024 * 1024 + 1000
            if current_size < target_size:
                padding = b'x' * (target_size - current_size)
                with open(log_path, 'ab') as f:
                    f.write(padding)
            print(f'Padded log file to {log_path.stat().st_size} bytes')
        
        # Trigger a log entry to cause rotation check
        client.ping()
        time.sleep(0.5)
        
        # Check files
        log_dir = home_path / 'logs'
        log_files = sorted(log_dir.glob('lmux.log*'))
        print(f'Log files ({len(log_files)}):')
        for f in log_files:
            print(f'  {f.name}: {f.stat().st_size} bytes')
    
finally:
    daemon.stop()
shutil.rmtree(home, ignore_errors=True)