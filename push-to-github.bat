@echo off
setlocal EnableExtensions EnableDelayedExpansion
title srv-t — Push to GitHub

REM ============================================================
REM  push-to-github.bat
REM  Run from the project folder (or double-click).
REM  You paste the GitHub repo URL when asked.
REM  Example URL: https://github.com/you/srv-t.git
REM               git@github.com:you/srv-t.git
REM ============================================================

cd /d "%~dp0"

echo.
echo  ========================================
echo   Push this project to GitHub
echo  ========================================
echo.
echo  Working directory:
echo    %CD%
echo.

where git >nul 2>&1
if errorlevel 1 (
  echo  ERROR: git is not installed or not in PATH.
  echo  Install Git for Windows: https://git-scm.com/download/win
  echo.
  pause
  exit /b 1
)

set /p REPO_URL=  GitHub repo URL: 
if "%REPO_URL%"=="" (
  echo  ERROR: URL is required.
  pause
  exit /b 1
)

set /p BRANCH=  Branch name [main]: 
if "%BRANCH%"=="" set BRANCH=main

set /p MSG=  Commit message [Update project]: 
if "%MSG%"=="" set MSG=Update project

echo.
echo  --- Preparing git repo ---

if not exist ".git" (
  echo  git init
  git init
  if errorlevel 1 goto :fail
  git branch -M %BRANCH%
) else (
  echo  Existing .git found
)

REM Ensure .gitignore exists so secrets/db/venv stay local
if not exist ".gitignore" (
  echo  Creating default .gitignore
  (
    echo .env
    echo *.db
    echo *.db-*
    echo __pycache__/
    echo *.pyc
    echo .venv/
    echo venv/
    echo *.log
    echo .DS_Store
    echo Thumbs.db
    echo .idea/
    echo .vscode/
  ) > .gitignore
)

echo.
echo  --- Staging files ---
git add -A
if errorlevel 1 goto :fail

git diff --cached --quiet
if %errorlevel%==0 (
  echo  Nothing new to commit — will push current history if any.
) else (
  echo  git commit
  git commit -m "%MSG%"
  if errorlevel 1 goto :fail
)

echo.
echo  --- Remote ---
git remote get-url origin >nul 2>&1
if errorlevel 1 (
  echo  git remote add origin %REPO_URL%
  git remote add origin "%REPO_URL%"
) else (
  echo  Updating origin → %REPO_URL%
  git remote set-url origin "%REPO_URL%"
)

echo.
echo  --- Push %BRANCH% ---
git push -u origin %BRANCH%
if errorlevel 1 (
  echo.
  echo  Push failed. Common fixes:
  echo    1. Create the empty repo on GitHub first (no README if you already have commits)
  echo    2. Log in:  gh auth login   OR use a Personal Access Token as password
  echo    3. If remote has commits you lack:  git pull origin %BRANCH% --rebase
  echo       then run this bat again
  echo.
  pause
  exit /b 1
)

echo.
echo  ========================================
echo   Done. Repo: %REPO_URL%
echo   Branch: %BRANCH%
echo  ========================================
echo.
pause
exit /b 0

:fail
echo.
echo  ERROR: command failed.
pause
exit /b 1
