@echo off
REM Copyright (c) KMG. All Rights Reserved.
REM
REM Licensed under the Apache License, Version 2.0 (the "License");
REM you may not use this file except in compliance with the License.
REM You may obtain a copy of the License at
REM
REM     http://www.apache.org/licenses/LICENSE-2.0
REM
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0sbk-dashboard.ps1" %*
exit /b %ERRORLEVEL%
