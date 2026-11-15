#!/bin/sh
set -eu
# No environment file, pip, migrations, existing-service activation or shell eval.
exec /usr/bin/python3 -I -S -B "$(dirname "$0")/manage.py" install "$@"
