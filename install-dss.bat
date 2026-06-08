@echo off
setlocal EnableExtensions EnableDelayedExpansion
title DSS Installer
color 0B

rem ============================================================
rem  DSS one-click bootstrap for Windows
rem  - installs Git + Python (via winget) if missing
rem  - clones / updates github.com/haitovs/dss
rem  - hands off to start.ps1 (venv + deps + launch)
rem ============================================================

set "REPO_URL=https://github.com/haitovs/dss.git"
set "REPO_BRANCH=v2"
set "INSTALL_DIR=%~dp0dss"
set "PY_WINGET_ID=Python.Python.3.12"
set "GIT_WINGET_ID=Git.Git"

call :banner "Starting DSS setup..."

rem --- [1/5] winget present? ----------------------------------
call :step "1/5" "Checking package manager (winget)"
where winget >nul 2>&1
if errorlevel 1 (
    call :fail "winget not found. Install 'App Installer' from the Microsoft Store, then re-run this script."
    goto :abort
)
call :ok "winget available"

rem --- [2/5] Git ----------------------------------------------
call :step "2/5" "Checking Git"
where git >nul 2>&1
if errorlevel 1 (
    call :info "Git not found - installing %GIT_WINGET_ID% ..."
    winget install -e --id %GIT_WINGET_ID% --accept-source-agreements --accept-package-agreements --silent
    call :refresh_path
    where git >nul 2>&1
    if errorlevel 1 (
        call :fail "Git install finished but 'git' is still not on PATH. Close this window and run the script again."
        goto :abort
    )
    call :ok "Git installed"
) else (
    call :ok "Git already present"
)

rem --- [3/5] Python -------------------------------------------
call :step "3/5" "Checking Python"
where py >nul 2>&1
if errorlevel 1 (
    call :info "Python launcher not found - installing %PY_WINGET_ID% ..."
    winget install -e --id %PY_WINGET_ID% --accept-source-agreements --accept-package-agreements --silent
    call :refresh_path
    where py >nul 2>&1
    if errorlevel 1 (
        call :fail "Python install finished but 'py' is still not on PATH. Close this window and run the script again."
        goto :abort
    )
    call :ok "Python installed"
) else (
    call :ok "Python already present"
)

rem --- [4/5] Get the project ----------------------------------
call :step "4/5" "Fetching project"
if exist "%INSTALL_DIR%\.git" (
    call :info "Repo exists - switching to '%REPO_BRANCH%' and pulling ..."
    git -C "%INSTALL_DIR%" fetch origin %REPO_BRANCH%
    if errorlevel 1 (
        call :fail "git fetch failed. Check your network."
        goto :abort
    )
    git -C "%INSTALL_DIR%" checkout %REPO_BRANCH%
    git -C "%INSTALL_DIR%" pull --ff-only origin %REPO_BRANCH%
    if errorlevel 1 (
        call :fail "git pull failed. Check your network or local changes in %INSTALL_DIR%."
        goto :abort
    )
    call :ok "Project updated on branch %REPO_BRANCH%"
) else (
    call :info "Cloning %REPO_URL% (branch %REPO_BRANCH%) ..."
    git clone --branch %REPO_BRANCH% "%REPO_URL%" "%INSTALL_DIR%"
    if errorlevel 1 (
        call :fail "git clone failed. Check your network and that the repo/branch is reachable."
        goto :abort
    )
    call :ok "Project cloned to %INSTALL_DIR% (branch %REPO_BRANCH%)"
)

rem --- [5/5] Launch ------------------------------------------
call :step "5/5" "Launching DSS (deps install on first run, may take a few minutes)"
if not exist "%INSTALL_DIR%\start.ps1" (
    call :fail "start.ps1 not found in %INSTALL_DIR% - repo layout changed?"
    goto :abort
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%INSTALL_DIR%\start.ps1"
if errorlevel 1 (
    call :fail "start.ps1 reported an error. Scroll up for details."
    goto :abort
)

echo.
echo  ============================================================
echo   DSS is running. Frontend: http://localhost:8080
echo   Login: admin / admin  (you'll set a new password)
echo  ============================================================
echo.
pause
goto :eof

rem ===================== helpers =============================

:banner
cls
echo.
echo   ____  ____  ____
echo  ^|  _ \^|  _ \^| ___^|   Dahua Stream Switch
echo  ^| ^| ^| ^| ^| ^| \___ \   centralised video gateway
echo  ^| ^|_^| ^| ^|_^| ^|___^) ^|
echo  ^|____/^|____/^|____/    %~1
echo.
goto :eof

:step
echo.
echo  [%~1] %~2
goto :eof

:ok
echo      [OK] %~1
goto :eof

:info
echo      ... %~1
goto :eof

:fail
color 0C
echo.
echo  [ERROR] %~1
goto :eof

rem Re-read PATH from the registry so freshly-installed tools are
rem visible without reopening the terminal.
:refresh_path
for /f "skip=2 tokens=2,*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SysPath=%%b"
for /f "skip=2 tokens=2,*" %%a in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "UsrPath=%%b"
set "PATH=%SysPath%;%UsrPath%;%ProgramFiles%\Git\cmd;%LocalAppData%\Programs\Git\cmd"
goto :eof

:abort
echo.
echo  Setup aborted. Nothing else will run.
echo.
pause
exit /b 1
