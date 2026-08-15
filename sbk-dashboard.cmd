@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0sbk-dashboard.ps1" %*
exit /b %ERRORLEVEL%
