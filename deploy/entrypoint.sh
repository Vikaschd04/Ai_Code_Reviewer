#!/bin/sh
# Make the platform-mounted data disk writable for the unprivileged user, then drop privileges
# for good (the platform's environment, including secrets, is kept). Everything - API, worker,
# analyzers and Temporal - runs as uid 10001.
set -eu
DATA="${CRP_DATA_DIR:-/data}"
if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA"
  chown -R crp:crp "$DATA"
  exec setpriv --reuid=10001 --regid=10001 --init-groups env HOME=/home/crp "$0" "$@"
fi
exec crp-dev hosted "$@"
