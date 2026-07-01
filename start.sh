#!/bin/sh
set -e

ROLE="${ROLE:-main}"

if [ "$ROLE" = "worker" ]; then
    exec python -m worker
else
    exec python -m entrypoint
fi
