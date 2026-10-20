#!/bin/sh
# Small launcher; the commands live in scripts/dev.py.
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
version_check='import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'

if [ -n "${SETUP_SSH_PYTHON:-}" ]; then
    if "$SETUP_SSH_PYTHON" -c "$version_check" 2>/dev/null; then
        exec "$SETUP_SSH_PYTHON" "$project_dir/scripts/dev.py" "$@"
    fi
else
    for python_command in python3 python; do
        if command -v "$python_command" >/dev/null 2>&1 &&
            "$python_command" -c "$version_check" 2>/dev/null; then
            exec "$python_command" "$project_dir/scripts/dev.py" "$@"
        fi
    done
fi

case "${1:-help}" in
    help|-h|--help)
        printf '%s\n' \
            'Usage: ./dev.sh <command> [args]' \
            'Commands: help doctor install reinstall uninstall develop run check fmt test build' \
            'Install Python 3.11+ and uv to use these commands. See docs/development.md.'
        ;;
    *)
        printf '%s\n' 'Python 3.11+ is required. Install it or set SETUP_SSH_PYTHON to its executable.' >&2
        exit 1
        ;;
esac
