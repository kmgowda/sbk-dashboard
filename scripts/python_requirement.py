#!/usr/bin/env python3
# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

"""Syntax-compatible source-launcher Python requirement check."""

import sys

MINIMUM_PYTHON = (3, 10)


def minimum_text() -> str:
    return ".".join(str(value) for value in MINIMUM_PYTHON)


def ensure_supported() -> None:
    if sys.version_info < MINIMUM_PYTHON:
        raise SystemExit(
            f"Python {minimum_text()} or newer is required; selected interpreter reports {sys.version.split()[0]}."
        )


if __name__ == "__main__":
    ensure_supported()
