import hashlib
import io
import os
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from sbk_dashboard.bootstrap import NativeToolBootstrap
from sbk_dashboard.config import DownloadConfig, RuntimePlatform, ToolArchive


class BootstrapTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_extracts_tar_and_rejects_traversal(self):
        archive = self.directory / "valid.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            data = b"binary"
            member = tarfile.TarInfo("prom/bin/prometheus")
            member.size = len(data)
            output.addfile(member, io.BytesIO(data))
        destination = self.directory / "out"
        destination.mkdir()
        NativeToolBootstrap._extract(archive, destination, "tar.gz")
        self.assertEqual(b"binary", (destination / "prom/bin/prometheus").read_bytes())
        invalid = self.directory / "invalid.tar.gz"
        with tarfile.open(invalid, "w:gz") as output:
            member = tarfile.TarInfo("../escape")
            member.size = 1
            output.addfile(member, io.BytesIO(b"x"))
        with self.assertRaisesRegex(OSError, "escapes"):
            NativeToolBootstrap._extract(invalid, destination, "tar.gz")

    def test_extracts_zip_and_rejects_traversal(self):
        archive = self.directory / "valid.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("grafana/bin/grafana.exe", b"binary")
        destination = self.directory / "zip"
        destination.mkdir()
        NativeToolBootstrap._extract(archive, destination, "zip")
        self.assertTrue((destination / "grafana/bin/grafana.exe").is_file())
        invalid = self.directory / "invalid.zip"
        with zipfile.ZipFile(invalid, "w") as output:
            output.writestr("../../escape", b"x")
        with self.assertRaisesRegex(OSError, "escapes"):
            NativeToolBootstrap._extract(invalid, destination, "zip")

    def test_installs_cached_verified_archive(self):
        downloads = self.directory / "downloads"
        installs = self.directory / "tools"
        downloads.mkdir()
        archive_path = downloads / "prom.tar.gz"
        with tarfile.open(archive_path, "w:gz") as output:
            data = b"#!/bin/sh\n"
            member = tarfile.TarInfo("prom/prometheus")
            member.mode = 0o644
            member.size = len(data)
            output.addfile(member, io.BytesIO(data))
        definition = ToolArchive("https://example.test/prom.tar.gz", archive_path.name,
                                 hashlib.sha256(archive_path.read_bytes()).hexdigest(), Path("prom"),
                                 Path("prometheus"), "tar.gz")
        config = DownloadConfig(downloads, installs, definition, definition, RuntimePlatform("linux", "x86_64"),
                                "test")
        home = NativeToolBootstrap()._install("Prometheus", definition, config)
        self.assertTrue(os.access(home / "prometheus", os.X_OK))


if __name__ == "__main__":
    unittest.main()
