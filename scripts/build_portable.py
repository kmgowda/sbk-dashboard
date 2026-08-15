#!/usr/bin/env python3
"""Build a self-contained, checksummed portable release archive on the current platform."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def application_version() -> str:
    namespace: dict[str, object] = {}
    exec((ROOT / "src/sbk_dashboard/version.py").read_text(encoding="utf-8"), namespace)
    return str(namespace["VERSION"])


def current_platform() -> str:
    operating_system = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")
    architecture = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "amd64"
    return f"{operating_system}-{architecture}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_bundle(output_directory: Path) -> Path:
    version = application_version()
    target = current_platform()
    bundle_name = f"sbk-dashboard-{version}-{target}"
    output_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sbk-dashboard-portable-") as temporary:
        work = Path(temporary)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "--onedir",
                "--name",
                "sbk-dashboard",
                "--paths",
                str(ROOT / "src"),
                "--collect-data",
                "sbk_dashboard",
                "--hidden-import",
                "sbk_dashboard.guardian",
                "--distpath",
                str(work / "dist"),
                "--workpath",
                str(work / "work"),
                "--specpath",
                str(work),
                str(ROOT / "scripts/sbk_dashboard_portable_entry.py"),
            ],
            check=True,
            cwd=ROOT,
        )
        bundle = work / bundle_name
        shutil.copytree(work / "dist" / "sbk-dashboard", bundle)
        shutil.copy2(ROOT / "LICENSE", bundle / "LICENSE")
        shutil.copy2(ROOT / "README.md", bundle / "README.md")
        shutil.copytree(ROOT / "docs", bundle / "docs")
        files = {
            path.relative_to(bundle).as_posix(): sha256(path) for path in sorted(bundle.rglob("*")) if path.is_file()
        }
        (bundle / "manifest.json").write_text(
            json.dumps({"version": version, "platform": target, "files": files}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if os.name == "nt":
            archive = output_directory / f"{bundle_name}.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
                for path in sorted(bundle.rglob("*")):
                    if path.is_file():
                        output.write(path, Path(bundle_name) / path.relative_to(bundle))
        else:
            archive = output_directory / f"{bundle_name}.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                output.add(bundle, arcname=bundle_name)
    (archive.with_suffix(archive.suffix + ".sha256")).write_text(
        f"{sha256(archive)}  {archive.name}\n", encoding="utf-8"
    )
    return archive


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "portable")
    selected = parser.parse_args()
    archive = build_bundle(selected.output.resolve())
    print(f"Built {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
