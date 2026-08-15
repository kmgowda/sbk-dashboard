#!/usr/bin/env python3
"""Syntax-compatible source-launcher Python requirement check."""

import sys

MINIMUM_PYTHON = (3, 10)


def minimum_text():
    return ".".join(str(value) for value in MINIMUM_PYTHON)


def ensure_supported():
    if sys.version_info < MINIMUM_PYTHON:
        raise SystemExit(
            f"Python {minimum_text()} or newer is required; selected interpreter reports {sys.version.split()[0]}."
        )


if __name__ == "__main__":
    ensure_supported()
