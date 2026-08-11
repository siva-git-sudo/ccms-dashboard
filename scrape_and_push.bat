@echo off
REM One-click daily job: scrape CCMS, then publish the result.
REM
REM Double-click this file, or run it from a prompt:
REM
REM   scrape_and_push.bat            scrape, commit the snapshot, push
REM   scrape_and_push.bat --visible  same, but show the browser window
REM   scrape_and_push.bat --all      every Forest division, not divisions.json
REM
REM Unattended by design -- no prompts, safe to point a scheduled task at.
REM If the scrape fails, nothing is committed and nothing is published:
REM yesterday's good data stays live rather than being replaced by empty
REM numbers, which would also poison tomorrow's up/down arrows.

setlocal enabledelayedexpansion
cd /d "%~dp0"
set "EXITCODE=0"

echo ============================================================
echo   CCMS daily scrape and publish
echo   %date% %time%
echo ============================================================
echo.

REM ----------------------------------------------------------- 1. scrape
call "%~dp0run_scrape.bat" %*
set "SCRAPE=!errorlevel!"

if not !SCRAPE!==0 (
    echo.
    echo ============================================================
    echo   SCRAPE FAILED ^(exit !SCRAPE!^) -- nothing published.
    echo   Previous data is untouched. See logs\latest.log
    echo ============================================================
    set "EXITCODE=!SCRAPE!"
    goto :finish
)

REM ------------------------------------------------------------ 2. publish
echo.
echo === publishing ===

where git >nul 2>&1
if not !errorlevel!==0 (
    echo WARNING: git not on PATH -- scrape data is saved but not published.
    goto :finish
)

if not exist ".git" (
    echo WARNING: not a git repository -- scrape data is saved but not published.
    goto :finish
)

if exist ".git\MERGE_HEAD" (
    echo WARNING: a merge is in progress -- skipping publish.
    echo Finish it by hand, then run push.bat
    goto :finish
)

REM Stage only what the scrape regenerates. Unrelated edits sitting in the
REM working tree stay yours to commit deliberately -- a scheduled job has
REM no business committing work in progress.
git add data/snapshots public/data.json public/data.js

git diff --staged --quiet
if !errorlevel!==0 (
    echo No data changes -- today's numbers match the last snapshot.
    goto :finish
)

for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "TODAY=%%d"

git -c user.name="ccms-bot" -c user.email="ccms-bot@users.noreply.github.com" commit -m "CCMS snapshot !TODAY!"
if not !errorlevel!==0 (
    echo WARNING: commit failed -- scrape data is saved on disk but not published.
    set "EXITCODE=1"
    goto :finish
)
echo Committed snapshot for !TODAY!.

REM Pull before pushing, or the push is rejected whenever the GitHub
REM Actions bot has committed a snapshot of its own since your last run.
echo.
echo --- pulling remote changes ---
git pull --no-rebase --no-edit
if not !errorlevel!==0 (
    echo.
    echo WARNING: pull hit a conflict. Your snapshot is committed locally.
    echo Resolve it, then run push.bat:
    echo     git status
    echo     ...edit the conflicting files, git add them
    echo     git commit
    echo     push.bat
    set "EXITCODE=1"
    goto :finish
)

echo.
echo --- pushing to origin ---
git push
if not !errorlevel!==0 (
    echo.
    echo WARNING: push failed. Your snapshot is committed locally -- nothing
    echo is lost. Check credentials / remote, then run: git push
    set "EXITCODE=1"
    goto :finish
)

echo.
echo ============================================================
echo   DONE. Snapshot published.
echo   https://github.com/siva-git-sudo/ccms-dashboard/actions
echo ============================================================

:finish
REM If this was double-clicked from Explorer, CMD closes the window the
REM moment we exit and you never get to read the output. Detect that and
REM wait for a keypress. Run from an open prompt and it exits normally.
echo %cmdcmdline% | find /i "%~nx0" >nul
if not errorlevel 1 (
    echo.
    pause
)
exit /b %EXITCODE%
