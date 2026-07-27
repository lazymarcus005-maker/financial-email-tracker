#!/bin/sh
set -eu

for dir in /app/data /app/secrets /app/logs; do
    mkdir -p "$dir"
done

if chown -R appuser:appuser /app/data /app/secrets /app/logs 2>/dev/null; then
    exec gosu appuser "$@"
fi

echo "Warning: could not chown mounted app directories; running as root so mounted secrets remain readable." >&2
exec "$@"
