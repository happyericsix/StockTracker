@echo off
REM ============================================================
REM  Double-click this file to start the whole stack.
REM  (No terminal typing needed.)
REM
REM  It is only a wrapper around start-all.ps1:
REM  a .ps1 opens in Notepad when double-clicked, a .bat runs.
REM
REM  To see status or stop everything, make a shortcut to this file
REM  and append one of these to the shortcut Target:
REM      -Status    show service status only
REM      -Stop      stop Python / Java / frontend
REM  (Chinese notes live in docs/START-WITHOUT-TERMINAL.md)
REM
REM  ASCII only on purpose: cmd.exe parses .bat with the OEM code page,
REM  so non-ASCII comments get mangled and can break line parsing.
REM ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-all.ps1" %*
echo.
echo Press any key to close this window.
echo (Closing the window does NOT stop the services started above.)
pause >nul
