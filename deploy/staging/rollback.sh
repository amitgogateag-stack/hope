#!/bin/sh
set -eu
# Stop only the verified transient staging preview; retain evidence and files.
exec /usr/bin/python3 -I -S -B "$(dirname "$0")/manage.py" rollback "$@"
