#!/bin/bash
exec >> /Users/you/.semantic-index/logs/watcher-stderr.log 2>&1
echo "=== Starting watcher at $(date) ==="
export PATH="/Users/you/.semantic-index/.venv/bin:/usr/local/bin:/usr/bin:/bin"
export VIRTUAL_ENV="/Users/you/.semantic-index/.venv"
export PYTHONPATH="/Users/you/.semantic-index"
export HOME="/Users/you"
cd /Users/you/.semantic-index
exec /Users/you/.semantic-index/.venv/bin/python3 /Users/you/.semantic-index/watcher.py
