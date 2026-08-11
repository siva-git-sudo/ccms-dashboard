@echo off
REM Windows wrapper for the CCMS scrape.
REM
REM   run_scrape.bat            scrape only
REM   run_scrape.bat --push     scrape, then commit + push (Pages republishes)
REM   run_scrape.bat --visible  show the browser window (debugging)
REM   run_scrape.bat --all      every Forest division, not just divisions.json
REM
REM Flags can be combined:  run_scrape.bat --push --all

setlocal
cd /d "%~dp0"

where py >nul 2>&1
if %errorlevel%==0 (
    py -3 run_scrape.py %*
) else (
    python run_scrape.py %*
)

set EXITCODE=%errorlevel%
if not %EXITCODE%==0 (
    echo.
    echo Scrape failed with exit code %EXITCODE%. See logs\latest.log
)

REM If this was double-clicked from Explorer, CMD closes the window the
REM moment we exit and you never get to read the output. Detect that and
REM wait for a keypress. Run from an open prompt and it exits normally.
echo %cmdcmdline% | find /i "%~nx0" >nul
if not errorlevel 1 (
    echo.
    pause
)
exit /b %EXITCODE%
