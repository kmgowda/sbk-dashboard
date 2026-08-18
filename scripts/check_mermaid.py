#!/usr/bin/env python3
# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

"""Discover and optionally render every Mermaid diagram in repository Markdown."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".git", ".venv", "build", "dist", "node_modules"}
SUPPORTED_DIAGRAM_PREFIXES = (
    "architecture-beta",
    "block-beta",
    "classDiagram",
    "erDiagram",
    "flowchart",
    "gitGraph",
    "journey",
    "mindmap",
    "pie",
    "quadrantChart",
    "requirementDiagram",
    "sequenceDiagram",
    "stateDiagram",
    "timeline",
    "xychart-beta",
)


@dataclass(frozen=True)
class MermaidBlock:
    source: Path
    line: int
    content: str


def markdown_files() -> list[Path]:
    """Return source Markdown files while ignoring generated and dependency trees."""
    return [
        path
        for path in sorted(ROOT.rglob("*.md"))
        if not any(part in EXCLUDED_PARTS for part in path.relative_to(ROOT).parts)
    ]


def blocks_in(path: Path) -> list[MermaidBlock]:
    """Extract closed Mermaid fences and report their source line."""
    blocks: list[MermaidBlock] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        if lines[index].strip() != "```mermaid":
            index += 1
            continue
        start = index + 1
        index += 1
        content: list[str] = []
        while index < len(lines) and lines[index].strip() != "```":
            content.append(lines[index])
            index += 1
        if index == len(lines):
            raise ValueError(f"{path.relative_to(ROOT)}:{start}: unclosed Mermaid fence")
        diagram = "\n".join(content).strip() + "\n"
        if not diagram.strip():
            raise ValueError(f"{path.relative_to(ROOT)}:{start}: empty Mermaid diagram")
        first = diagram.lstrip().splitlines()[0]
        if not first.startswith(SUPPORTED_DIAGRAM_PREFIXES):
            raise ValueError(
                f"{path.relative_to(ROOT)}:{start}: unsupported Mermaid diagram declaration {first!r}"
            )
        blocks.append(MermaidBlock(path, start, diagram))
        index += 1
    return blocks


def discover() -> list[MermaidBlock]:
    """Discover and structurally validate all Mermaid blocks."""
    return [block for path in markdown_files() for block in blocks_in(path)]


def render(blocks: list[MermaidBlock], renderer: str) -> None:
    """Render each diagram separately so failures identify the owning source line."""
    with tempfile.TemporaryDirectory(prefix="sbk-dashboard-mermaid-") as temporary:
        root = Path(temporary)
        browser_arguments: list[str] = []
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            browser_config = root / "puppeteer.json"
            browser_config.write_text(json.dumps({"args": ["--no-sandbox"]}), encoding="utf-8")
            browser_arguments = ["--puppeteerConfigFile", str(browser_config)]
        for number, block in enumerate(blocks, start=1):
            source = root / f"diagram-{number}.mmd"
            output = root / f"diagram-{number}.svg"
            source.write_text(block.content, encoding="utf-8")
            completed = subprocess.run(
                [
                    renderer,
                    "--input",
                    str(source),
                    "--output",
                    str(output),
                    "--backgroundColor",
                    "transparent",
                    *browser_arguments,
                ],
                capture_output=True,
                check=False,
                text=True,
                timeout=60,
            )
            if completed.returncode != 0 or not output.is_file():
                detail = completed.stderr.strip() or completed.stdout.strip() or "renderer produced no SVG"
                location = f"{block.source.relative_to(ROOT)}:{block.line}"
                raise RuntimeError(f"Mermaid render failed at {location}: {detail}")


def main(arguments: list[str] | None = None) -> int:
    """Validate Mermaid fences and use mmdc when it is available."""
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--require-renderer", action="store_true", help="fail when mmdc is not on PATH")
    options = command.parse_args(arguments)
    try:
        blocks = discover()
        if not blocks:
            raise ValueError("no Mermaid diagrams found")
        renderer = shutil.which("mmdc")
        if renderer is None:
            if options.require_renderer:
                raise RuntimeError("mmdc is required; install @mermaid-js/mermaid-cli")
            print(f"Validated {len(blocks)} Mermaid blocks structurally; mmdc is not installed.")
            return 0
        render(blocks, renderer)
        print(f"Rendered {len(blocks)} Mermaid diagrams successfully with {renderer}.")
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
