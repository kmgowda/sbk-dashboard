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

PYTHON=
ENVIRONMENT=
if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    PYTHON="$VIRTUAL_ENV/bin/python"
    ENVIRONMENT="active virtual environment $VIRTUAL_ENV"
elif [ -n "${CONDA_PREFIX:-}" ] && [ -x "$CONDA_PREFIX/bin/python" ]; then
    PYTHON="$CONDA_PREFIX/bin/python"
    ENVIRONMENT="active Conda environment $CONDA_PREFIX"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON=$(command -v python3)
    ENVIRONMENT="Python on PATH"
else
    echo "Python 3.10+ was not found; switching to the verified standalone runtime." >&2
fi

if [ -n "$PYTHON" ] && "$PYTHON" "$SCRIPT_DIR/python_requirement.py" >/dev/null 2>&1; then
    [ "$MODE" = stop ] || echo "Selected $ENVIRONMENT"
    exec "$PYTHON" "$SCRIPT_DIR/sbk_dashboard_bootstrap.py" "$MODE" "$@"
fi

if [ -n "$PYTHON" ]; then
    "$PYTHON" "$SCRIPT_DIR/python_requirement.py" >&2 || true
    echo "Switching to the verified standalone runtime." >&2
fi
exec "$SCRIPT_DIR/install-portable.sh" "$MODE" "$@"
