#!/bin/sh
# Starts the container command as the unprivileged "app" user.
# Railway mounts volumes owned by root, so as root we first give the media folder to "app".
# Already running as a non-root user (e.g. RAILWAY_RUN_UID set): just run the command.
set -e

if [ "$(id -u)" = "0" ]; then
    media="${MEDIA_ROOT:-/data/media}"
    mkdir -p "$media"
    chown app:app "$media"
    exec setpriv --reuid=app --regid=app --init-groups "$@"
fi
exec "$@"
