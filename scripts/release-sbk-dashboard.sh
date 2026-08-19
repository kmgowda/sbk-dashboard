#!/bin/sh
# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON=
if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    PYTHON="$VIRTUAL_ENV/bin/python"
elif [ -n "${CONDA_PREFIX:-}" ] && [ -x "$CONDA_PREFIX/bin/python" ]; then
    PYTHON="$CONDA_PREFIX/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON=$(command -v python3)
fi

if [ -z "$PYTHON" ] || ! "$PYTHON" "$SCRIPT_DIR/python_requirement.py" >/dev/null 2>&1; then
    echo "SBK Dashboard release engineering requires Python 3.10+ and Git in a source checkout." >&2
    [ -z "$PYTHON" ] || "$PYTHON" "$SCRIPT_DIR/python_requirement.py" >&2 || true
    exit 1
fi

exec "$PYTHON" "$SCRIPT_DIR/release.py" "$@"
