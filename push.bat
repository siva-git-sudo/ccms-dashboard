@echo off
REM Commit local changes and push to GitHub, pulling the remote in first
REM so the push can't be rejected. GitHub Actions republishes the
REM dashboard from the resulting commit.
REM
REM   push.bat                      commit with an auto-dated message
REM   push.bat Fix division names   commit with your own message
REM
REM Order is deliberate: commit -> pull -> push. Committing first means
REM the merge has something to merge against and your work is never left
REM loose in the working tree while git rewrites files underneath it.
REM
REM For scrape output only, use run_scrape.bat --push -- that stages just
REM the snapshot files and leaves everything else alone.

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

REM A crashed git run leaves a lock behind and every later command fails
REM with a confusing "another git process seems to be running".
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

REM Refuse to run in the middle of a merge or rebase -- committing now
REM would bake in a half-finished state.
if exist ".git\MERGE_HEAD" (
    echo ERROR: a merge is in progress. Finish it first:
    echo     resolve the conflicts, git add them, then: git commit
    echo   or abandon it with: git merge --abort
    set "EXITCODE=1"
    goto :finish
)
if exist ".git\rebase-merge" goto :rebasing
if exist ".git\rebase-apply" goto :rebasing
goto :notrebasing
:rebasing
echo ERROR: a rebase is in progress. Finish it with: git rebase --continue
echo   or abandon it with: git rebase --abort
set "EXITCODE=1"
goto :finish
:notrebasing

REM ---------------------------------------------------------------- commit
echo === local changes ===
git status --short
echo.

git add -A
git diff --staged --quiet
if %errorlevel%==0 (
    echo Nothing new to commit.
    echo.
    goto :sync
)

REM Everything after the command name becomes the commit message.
set "MSG=%*"
if "%MSG%"=="" (
    for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "MSG=Update %%d"
)

echo Commit message: !MSG!
choice /c YN /m "Commit these changes"
if not !errorlevel!==1 (
    echo Aborted. Nothing committed, nothing pushed.
    git reset >nul
    set "EXITCODE=1"
    goto :finish
)
echo.

git commit -m "!MSG!"
if not !errorlevel!==0 (
    echo.
    echo ERROR: commit failed. Nothing was pushed.
    set "EXITCODE=1"
    goto :finish
)
echo.

REM ------------------------------------------------------------------ sync
:sync
echo === pulling remote changes ===
REM --no-edit keeps the merge from opening an editor. --no-rebase merges
REM rather than replays: if the same files changed on both sides you get
REM one conflict to settle, not the same conflict once per commit.
git pull --no-rebase --no-edit
if not %errorlevel%==0 (
    echo.
    echo ERROR: pull failed -- most likely a merge conflict.
    echo.
    echo   git status                      see which files conflict
    echo   ...edit them, then git add ^<file^>
    echo   git commit                      finish the merge
    echo   push.bat                        run this again
    echo.
    echo Or back out entirely with: git merge --abort
    set "EXITCODE=1"
    goto :finish
)
echo.

REM ------------------------------------------------------------------ push
echo === pushing to origin ===
git push
if not %errorlevel%==0 (
    echo.
    echo ERROR: push failed. Your commit is saved locally -- nothing is lost.
    echo Check your credentials / remote, then run: git push
    set "EXITCODE=1"
    goto :finish
)

echo.
echo Pushed. GitHub Actions will republish the dashboard from this commit.
echo   https://github.com/siva-git-sudo/ccms-dashboard/actions

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
