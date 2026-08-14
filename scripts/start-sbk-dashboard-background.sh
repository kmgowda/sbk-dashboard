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
    echo "Python 3.10 or newer is required, but python3 was not found." >&2
    echo "Install Python from https://www.python.org/downloads/ or your Linux/macOS package manager." >&2
    echo "Install Python with its venv module, then rerun this script." >&2
    exit 1
fi

if [ ! -x "$PYTHON" ]; then
    echo "The selected $ENVIRONMENT has no executable Python at $PYTHON." >&2
    echo "Reactivate a valid environment, or recreate the project environment:" >&2
    echo "  python3 -m venv .venv" >&2
    echo "  . .venv/bin/activate" >&2
    echo "  python -m pip install ." >&2
    exit 1
fi

if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    PYTHON_VERSION=$("$PYTHON" --version 2>&1 || true)
    echo "Python 3.10 or newer is required; selected interpreter reports: $PYTHON_VERSION" >&2
    echo "Install a supported Python, then recreate or reactivate the venv/Conda environment." >&2
    exit 1
fi

echo "Selected $ENVIRONMENT"
exec "$PYTHON" "$SCRIPT_DIR/sbk_dashboard_bootstrap.py" background "$@"
