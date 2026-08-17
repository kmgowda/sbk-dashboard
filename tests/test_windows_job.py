# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sbk_dashboard.windows_job import (
    CREATE_NEW_PROCESS_GROUP,
    CREATE_SUSPENDED,
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    PROCESS_SET_QUOTA,
    PROCESS_TERMINATE,
    THREAD_SUSPEND_RESUME,
    WindowsKillOnCloseJob,
)


class WindowsJobTest(unittest.TestCase):
    @staticmethod
    def kernel32():
        kernel = MagicMock()
        kernel.CreateJobObjectW.return_value = 101
        kernel.SetInformationJobObject.return_value = 1
        kernel.OpenProcess.return_value = 202
        kernel.AssignProcessToJobObject.return_value = 1
        kernel.OpenThread.return_value = 303
        kernel.ResumeThread.return_value = 1
        kernel.CloseHandle.return_value = 1
        return kernel

    def test_job_assigns_suspended_process_and_resumes_its_primary_thread(self):
        kernel = self.kernel32()
        process = MagicMock()
        process.threads.return_value = [SimpleNamespace(id=404)]
        with patch("sbk_dashboard.windows_job.psutil.Process", return_value=process):
            job = WindowsKillOnCloseJob(kernel)
            job.assign_and_resume(505)
            job.close()

        kernel.SetInformationJobObject.assert_called_once()
        set_info = kernel.SetInformationJobObject.call_args.args
        self.assertEqual(101, set_info[0])
        self.assertEqual(JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS, set_info[1])
        limits = set_info[2]._obj
        self.assertEqual(
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            limits.basic_limit_information.limit_flags,
        )
        kernel.OpenProcess.assert_called_once_with(
            PROCESS_TERMINATE | PROCESS_SET_QUOTA,
            False,
            505,
        )
        kernel.AssignProcessToJobObject.assert_called_once_with(101, 202)
        kernel.OpenThread.assert_called_once_with(THREAD_SUSPEND_RESUME, False, 404)
        kernel.ResumeThread.assert_called_once_with(303)
        self.assertEqual([202, 303, 101], [call.args[0] for call in kernel.CloseHandle.call_args_list])

    def test_assignment_failure_closes_opened_process_and_job_handles(self):
        kernel = self.kernel32()
        kernel.AssignProcessToJobObject.return_value = 0
        job = WindowsKillOnCloseJob(kernel)
        with self.assertRaisesRegex(OSError, "AssignProcessToJobObject"):
            job.assign_and_resume(505)
        job.close()
        self.assertEqual([202, 101], [call.args[0] for call in kernel.CloseHandle.call_args_list])

    def test_windows_creation_flags_suspend_before_job_assignment(self):
        self.assertEqual(0x00000004, CREATE_SUSPENDED)
        self.assertEqual(0x00000200, CREATE_NEW_PROCESS_GROUP)


if __name__ == "__main__":
    unittest.main()
