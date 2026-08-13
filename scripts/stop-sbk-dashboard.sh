#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(dirname -- "$SCRIPT_DIR")

if [ -n "${VIRTUAL_ENV:-}" ]; then
    PYTHON="$VIRTUAL_ENV/bin/python"
    ENVIRONMENT="active virtual environment $VIRTUAL_ENV"
elif [ -n "${CONDA_PREFIX:-}" ]; then
    PYTHON="$CONDA_PREFIX/bin/python"
    ENVIRONMENT="active Conda environment $CONDA_PREFIX"
elif [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
    PYTHON="$PROJECT_DIR/.venv/bin/python"
    ENVIRONMENT="project virtual environment $PROJECT_DIR/.venv"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON=$(command -v python3)
    ENVIRONMENT="Python on PATH"
else
    echo "Python 3.10 or newer is required to run the stop helper, but python3 was not found." >&2
    exit 1
fi

if [ ! -x "$PYTHON" ]; then
    echo "The selected $ENVIRONMENT has no executable Python at $PYTHON." >&2
    echo "Reactivate the environment used to start SBK Dashboard." >&2
    exit 1
fi

if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    PYTHON_VERSION=$("$PYTHON" --version 2>&1 || true)
    echo "Python 3.10 or newer is required; selected interpreter reports: $PYTHON_VERSION" >&2
    echo "Reactivate the supported environment used to start SBK Dashboard." >&2
    exit 1
fi

exec "$PYTHON" "$SCRIPT_DIR/sbk_dashboard_launcher.py" stop
