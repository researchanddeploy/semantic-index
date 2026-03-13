#!/bin/bash
exec >> /Users/you/.semantic-index/logs/hayhooks-stderr.log 2>&1
echo "=== Starting hayhooks at $(date) ==="
export PATH="/Users/you/.semantic-index/.venv/bin:/usr/local/bin:/usr/bin:/bin"
export VIRTUAL_ENV="/Users/you/.semantic-index/.venv"
export PYTHONPATH="/Users/you/.semantic-index"
export HOME="/Users/you"
cd /Users/you/.semantic-index
exec /Users/you/.semantic-index/.venv/bin/hayhooks mcp run \
  --pipelines-dir /Users/you/.semantic-index/pipelines \
  --additional-python-path /Users/you/.semantic-index
