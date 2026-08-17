# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sbk_dashboard.files import atomic_write


class AtomicWriteTest(unittest.TestCase):
    def test_replaces_content_and_fsyncs_file_and_posix_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            with patch("sbk_dashboard.files.os.fsync", wraps=os.fsync) as sync:
                atomic_write(path, b"first")
            self.assertEqual(b"first", path.read_bytes())
            self.assertEqual(1 if os.name == "nt" else 2, sync.call_count)

    def test_failed_replace_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            with (
                patch("sbk_dashboard.files.os.replace", side_effect=OSError("replace failed")),
                self.assertRaisesRegex(OSError, "replace failed"),
            ):
                atomic_write(path, b"value")
            self.assertEqual([], list(Path(temporary).glob(".state.json.*")))


if __name__ == "__main__":
    unittest.main()
