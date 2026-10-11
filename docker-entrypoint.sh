#!/bin/sh
set -eu
# Cloud volume mounts may be root-owned even if the image's /data is not.
# Fix the mount ownership while privileged, then run the web app unprivileged.
if [ "$(id -u)" -eq 0 ]; then
  mkdir -p /data
  chown -R koli:koli /data
  exec gosu koli "$@"
fi
exec "$@"

