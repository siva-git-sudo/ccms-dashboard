@echo off
REM Commit everything in the working tree and push to GitHub.
REM GitHub Pages then republishes the dashboard from the new commit.
REM
REM   push.bat                      commit with an auto-dated message
REM   push.bat Fix division names   commit with your own message
REM
REM For scrape output only, use run_scrape.bat --push instead -- that
REM stages just the snapshot files and leaves work-in-progress alone.

setlocal enabledelayedexpansion
cd /d "%~dp0"
set "EXITCODE=0"

where git >nul 2>&1
if not %errorlevel%==0 (
    echo ERROR: git is not on your PATH.
    echo Install Git for Windows from https://git-scm.com/download/win
    set "EXITCODE=1"
    goto :finish
)

if not exist ".git" (
    echo ERROR: %cd% is not a git repository.
    set "EXITCODE=1"
    goto :finish
)

REM A stale lock from a crashed git run blocks everything with a
REM confusing "another git process seems to be running" message.
if exist ".git\index.lock" (
    echo WARNING: found a leftover .git\index.lock
    echo Make sure no other git client ^(VS Code, GitHub Desktop^) is running.
    choice /c YN /m "Delete the lock and continue"
    if !errorlevel!==1 (
        del ".git\index.lock"
        echo Lock removed.
        echo.
    ) else (
        echo Aborted.
        set "EXITCODE=1"
        goto :finish
    )
)

REM Everything after the command name becomes the commit message.
set "MSG=%*"
if "%MSG%"=="" (
    for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "MSG=Update %%d"
)

echo === changes to be committed ===
git add -A
git status --short
echo.

git diff --staged --quiet
if %errorlevel%==0 (
    echo Nothing to commit -- working tree is clean.
    goto :push
)

git commit -m "!MSG!"
if not %errorlevel%==0 (
    echo.
    echo ERROR: commit failed. Nothing was pushed.
    set "EXITCODE=1"
    goto :finish
)
echo Committed: !MSG!
echo.

:push
echo === pushing to origin ===
git push
if not %errorlevel%==0 (
    echo.
    echo ERROR: push failed. Your commit is saved locally.
    echo Check your credentials / remote, then run: git push
    set "EXITCODE=1"
    goto :finish
)

echo.
echo Pushed. GitHub Actions will republish the dashboard from this commit.
echo Watch it at: https://github.com/siva-git-sudo/ccms-dashboard/actions

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
