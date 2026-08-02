import hashlib
import io
import os
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

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

    def test_download_promotes_complete_content(self):
        destination = self.directory / "tool.tar.gz"
        response = io.BytesIO(b"downloaded archive")
        response.headers = {"Content-Length": str(len(b"downloaded archive"))}
        with patch("sbk_dashboard.bootstrap.urllib.request.urlopen", return_value=response):
            NativeToolBootstrap._download("Tool", "https://example.test/tool.tar.gz", destination)
        self.assertEqual(b"downloaded archive", destination.read_bytes())
        self.assertFalse(destination.with_name(destination.name + ".part").exists())

    def test_download_failure_removes_partial_content(self):
        destination = self.directory / "tool.tar.gz"

        class FailingResponse(io.BytesIO):
            headers = {"Content-Length": "100"}

            def read(self, size=-1):
                if self.tell():
                    raise OSError("connection lost")
                return super().read(3)

        with patch(
            "sbk_dashboard.bootstrap.urllib.request.urlopen", return_value=FailingResponse(b"partial")
        ), self.assertRaisesRegex(OSError, "connection lost"):
            NativeToolBootstrap._download("Tool", "https://example.test/tool.tar.gz", destination)
        self.assertFalse(destination.exists())
        self.assertFalse(destination.with_name(destination.name + ".part").exists())

    def test_download_rejects_negative_content_length_and_removes_partial_file(self):
        destination = self.directory / "tool.tar.gz"
        response = io.BytesIO(b"downloaded archive")
        response.headers = {"Content-Length": "-1"}
        with (
            patch("sbk_dashboard.bootstrap.urllib.request.urlopen", return_value=response),
            self.assertRaisesRegex(OSError, "negative Content-Length"),
        ):
            NativeToolBootstrap._download("Tool", "https://example.test/tool.tar.gz", destination)
        self.assertFalse(destination.exists())
        self.assertFalse(destination.with_name(destination.name + ".part").exists())

    def test_download_wraps_non_numeric_content_length_as_io_failure(self):
        destination = self.directory / "tool.tar.gz"
        response = io.BytesIO(b"downloaded archive")
        response.headers = {"Content-Length": "not-a-number"}
        with (
            patch("sbk_dashboard.bootstrap.urllib.request.urlopen", return_value=response),
            self.assertRaisesRegex(OSError, "invalid Content-Length"),
        ):
            NativeToolBootstrap._download("Tool", "https://example.test/tool.tar.gz", destination)
        self.assertFalse(destination.exists())
        self.assertFalse(destination.with_name(destination.name + ".part").exists())

    def test_invalid_cached_archive_is_downloaded_again(self):
        downloads = self.directory / "downloads"
        installs = self.directory / "tools"
        downloads.mkdir()
        archive_path = downloads / "prom.tar.gz"
        archive_path.write_bytes(b"invalid")
        valid_archive = self.directory / "valid.tar.gz"
        with tarfile.open(valid_archive, "w:gz") as output:
            data = b"binary"
            member = tarfile.TarInfo("prom/prometheus")
            member.size = len(data)
            output.addfile(member, io.BytesIO(data))
        definition = ToolArchive(
            "https://example.test/prom.tar.gz",
            archive_path.name,
            hashlib.sha256(valid_archive.read_bytes()).hexdigest(),
            Path("prom"),
            Path("prometheus"),
            "tar.gz",
        )
        config = DownloadConfig(
            downloads, installs, definition, definition, RuntimePlatform("linux", "x86_64"), "test"
        )

        def download(_name, _url, destination):
            destination.write_bytes(valid_archive.read_bytes())

        with patch.object(NativeToolBootstrap, "_download", side_effect=download) as downloaded:
            home = NativeToolBootstrap()._install("Prometheus", definition, config)
        downloaded.assert_called_once()
        self.assertTrue((home / "prometheus").is_file())


if __name__ == "__main__":
    unittest.main()
