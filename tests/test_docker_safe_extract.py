# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.docker_safe_extract import extract


class DockerSafeExtractTest(unittest.TestCase):
    def test_extract_removes_single_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "tool.tar.gz"
            payload = b"executable"
            with tarfile.open(archive, "w:gz") as output:
                member = tarfile.TarInfo("tool-version/bin/tool")
                member.size = len(payload)
                output.addfile(member, io.BytesIO(payload))
            destination = root / "output"
            extract(archive, destination)
            self.assertEqual(payload, (destination / "bin" / "tool").read_bytes())

    def test_extract_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "unsafe.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                output.addfile(tarfile.TarInfo("tool/../../escape"), io.BytesIO())
            with self.assertRaisesRegex(ValueError, "unsafe archive member"):
                extract(archive, root / "output")
            self.assertFalse((root / "escape").exists())


if __name__ == "__main__":
    unittest.main()
