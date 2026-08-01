"""Verified, cross-platform Prometheus and Grafana bootstrap."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from sbk_dashboard.config import (
    DownloadConfig,
    MonitoringConfig,
    RuntimePlatform,
    ToolArchive,
    executable,
    resolve_on_path,
)


class NativeToolBootstrap:
    """Resolve installed tools or download pinned official archives."""

    def resolve(self, monitoring: MonitoringConfig, downloads: DownloadConfig) -> MonitoringConfig:
        prometheus = resolve_on_path(monitoring.prometheus_binary, downloads.platform)
        if prometheus is None:
            print(f"Prometheus is not installed at {monitoring.prometheus_binary}; bootstrapping from properties")
            prometheus_home = self._install("Prometheus", downloads.prometheus, downloads)
            prometheus = prometheus_home / downloads.prometheus.executable
        else:
            print(f"Prometheus found at {prometheus}")
        grafana_executable = self._grafana_executable(monitoring.grafana_home, downloads.platform)
        grafana_home = monitoring.grafana_home.expanduser().resolve()
        if grafana_executable is None:
            print(f"Grafana is not installed under {monitoring.grafana_home}; bootstrapping from properties")
            grafana_home = self._install("Grafana", downloads.grafana, downloads)
            if self._grafana_executable(grafana_home, downloads.platform) is None:
                raise OSError(f"Grafana archive does not contain an executable under {grafana_home / 'bin'}")
        else:
            print(f"Grafana found at {grafana_home}")
        return monitoring.with_tools(prometheus.resolve(), grafana_home.resolve())

    def _install(self, name: str, archive: ToolArchive, config: DownloadConfig) -> Path:
        destination = config.install_directory / archive.archive_directory
        expected_executable = destination / archive.executable
        if executable(expected_executable, config.platform):
            print(f"{name} installed at {destination}")
            return destination
        config.download_directory.mkdir(parents=True, exist_ok=True)
        config.install_directory.mkdir(parents=True, exist_ok=True)
        downloaded = config.download_directory / archive.file_name
        if downloaded.is_file() and self._checksum(downloaded) != archive.sha256:
            print(f"WARNING: Cached {name} archive checksum is invalid; downloading it again")
            downloaded.unlink()
        if not downloaded.is_file():
            self._download(name, archive.url, downloaded)
        if self._checksum(downloaded) != archive.sha256:
            raise OSError(f"{name} download SHA-256 verification failed: {downloaded}")
        print(f"{name} download verified successfully")
        temporary = Path(tempfile.mkdtemp(prefix=f".{name.lower()}-", dir=config.install_directory))
        try:
            try:
                self._extract(downloaded, temporary, archive.archive_format)
            except (tarfile.TarError, zipfile.BadZipFile) as error:
                raise OSError(f"Unable to extract {name} archive: {error}") from error
            extracted = temporary / archive.archive_directory
            if not extracted.is_dir():
                raise OSError(f"{name} archive directory is missing: {archive.archive_directory}")
            source_executable = extracted / archive.executable
            if not source_executable.is_file():
                raise OSError(f"{name} archive executable is missing: {archive.executable}")
            if not config.platform.windows:
                source_executable.chmod(source_executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            if destination.exists():
                shutil.rmtree(destination)
            os.replace(extracted, destination)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        print(f"{name} installed at {destination}")
        return destination

    @staticmethod
    def _download(name: str, url: str, destination: Path) -> None:
        print(f"Downloading {name} from {url}")
        print(f"Download destination: {destination}")
        temporary = destination.with_name(destination.name + ".part")
        request = urllib.request.Request(url, headers={"User-Agent": "sbk-dashboard/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
                total = int(response.headers.get("Content-Length", "0"))
                downloaded = 0
                last_update = 0.0
                while True:
                    chunk = response.read(128 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    if now - last_update >= 0.25 or (total and downloaded >= total):
                        NativeToolBootstrap._progress(name, downloaded, total)
                        last_update = now
                output.flush()
                os.fsync(output.fileno())
            NativeToolBootstrap._progress(name, downloaded, total, complete=True)
            os.replace(temporary, destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _progress(name: str, downloaded: int, total: int, complete: bool = False) -> None:
        if total:
            message = f"{min(100.0, downloaded * 100 / total):.1f}% ({_bytes(downloaded)} / {_bytes(total)})"
        else:
            message = f"{_bytes(downloaded)} downloaded"
        print(f"\r{name} download progress: {message}", end="\n" if complete else "", flush=True)

    @staticmethod
    def _extract(archive: Path, destination: Path, archive_format: str) -> None:
        if archive_format == "tar.gz":
            with tarfile.open(archive, "r:gz") as source:
                for member in source.getmembers():
                    if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                        raise OSError(f"Unsupported archive entry: {member.name}")
                    _safe_member(member.name)
                if sys.version_info >= (3, 12):
                    source.extractall(destination, filter="data")
                else:
                    source.extractall(destination)
        elif archive_format == "zip":
            with zipfile.ZipFile(archive) as source:
                for member in source.infolist():
                    _safe_member(member.filename)
                    if (member.external_attr >> 16) & 0o170000 == 0o120000:
                        raise OSError(f"Archive links are not allowed: {member.filename}")
                source.extractall(destination)
        else:
            raise ValueError(f"Unsupported archive format: {archive_format}")

    @staticmethod
    def _checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _grafana_executable(home: Path, runtime_platform: RuntimePlatform) -> Path | None:
        home = home.expanduser().resolve()
        for base in ("grafana", "grafana-server"):
            names = (base + ".exe", base) if runtime_platform.windows else (base,)
            for name in names:
                candidate = home / "bin" / name
                if executable(candidate, runtime_platform):
                    return candidate
        return None


def _safe_member(name: str) -> None:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise OSError(f"Archive entry escapes extraction directory: {name}")


def _bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{int(amount)} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TiB"
