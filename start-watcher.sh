#!/bin/bash
APP_DIR="/Users/you/.semantic-index"

exec >> "$APP_DIR/logs/watcher-stderr.log" 2>&1
echo "=== Starting watcher at $(date) ==="
export PATH="$APP_DIR/.venv/bin:/usr/local/bin:/usr/bin:/bin"
export VIRTUAL_ENV="$APP_DIR/.venv"
export PYTHONPATH="$APP_DIR"
cd "$APP_DIR"
exec "$APP_DIR/.venv/bin/python3" "$APP_DIR/watcher.py"
