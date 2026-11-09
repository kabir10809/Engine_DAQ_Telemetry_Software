#!/bin/sh
# Start the application on Linux or macOS from any folder.
cd "$(dirname "$0")" || exit 1
for python in python3 python; do
    if command -v "$python" >/dev/null 2>&1; then
        exec "$python" launch_engine_daq.py "$@"
    fi
done
echo "Python 3.10 or newer is required. Install it from https://www.python.org/downloads/" >&2
exit 1
