#!/bin/sh
set -eu

PUID="${PUID:-99}"
PGID="${PGID:-100}"

if [ "$(id -u)" = "0" ]; then
    groupmod -o -g "$PGID" app
    usermod -o -u "$PUID" -g app app
    mkdir -p /config /data
    chown -R app:app /config /data
    exec runuser -u app -- "$@"
fi

exec "$@"
