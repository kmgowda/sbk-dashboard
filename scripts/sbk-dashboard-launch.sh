#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MODE=${1:-foreground}
[ "$#" -eq 0 ] || shift

case "$MODE" in
    foreground|background|stop|repair) ;;
    *)
        echo "Unknown SBK Dashboard launcher mode: $MODE" >&2
        exit 2
        ;;
esac

if [ -n "${VIRTUAL_ENV:-}" ]; then
    PYTHON="$VIRTUAL_ENV/bin/python"
    ENVIRONMENT="active virtual environment $VIRTUAL_ENV"
elif [ -n "${CONDA_PREFIX:-}" ]; then
    PYTHON="$CONDA_PREFIX/bin/python"
    ENVIRONMENT="active Conda environment $CONDA_PREFIX"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON=$(command -v python3)
    ENVIRONMENT="Python on PATH"
else
    echo "Python 3 is required, but python3 was not found." >&2
    echo "Install Python from https://www.python.org/downloads/ or your Linux/macOS package manager." >&2
    echo "Install Python with its venv module, then rerun this script." >&2
    exit 1
fi

if [ ! -x "$PYTHON" ]; then
    echo "The selected $ENVIRONMENT has no executable Python at $PYTHON." >&2
    echo "Reactivate a valid environment, or install a supported Python with venv support." >&2
    exit 1
fi

if ! "$PYTHON" "$SCRIPT_DIR/python_requirement.py" >/dev/null 2>&1; then
    "$PYTHON" "$SCRIPT_DIR/python_requirement.py"
    exit 1
fi

[ "$MODE" = stop ] || echo "Selected $ENVIRONMENT"
exec "$PYTHON" "$SCRIPT_DIR/sbk_dashboard_bootstrap.py" "$MODE" "$@"
