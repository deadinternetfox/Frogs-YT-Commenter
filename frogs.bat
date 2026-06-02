@echo off
REM frogs.bat - launcher for "Frogs YouTube Replier" on Windows.
REM Creates a local .venv, installs requirements once, then runs the app.
REM Usage:  frogs.bat            (launch)
REM         frogs.bat --reinstall (force reinstall deps, then launch)
setlocal enabledelayedexpansion

set "HERE=%~dp0"
set "VENV=%HERE%.venv"
set "PY=%VENV%\Scripts\python.exe"
set "SENTINEL=%VENV%\.frogs_installed"

REM Pick a Python launcher: prefer the 'py' launcher, fall back to 'python'.
where py >nul 2>nul && (set "BOOT=py -3") || (set "BOOT=python")

if /I "%~1"=="--reinstall" ( if exist "%SENTINEL%" del "%SENTINEL%" )

if not exist "%VENV%" (
  echo Frog: first run - creating virtual environment...
  %BOOT% -m venv "%VENV%"
  if errorlevel 1 (
    echo.
    echo Could not create a virtual environment.
    echo Install Python 3 from https://python.org and tick "Add Python to PATH".
    pause
    exit /b 1
  )
  "%PY%" -m pip install --quiet --upgrade pip
)

if not exist "%SENTINEL%" (
  echo Frog: installing dependencies ^(one time^)...
  "%PY%" -m pip install -r "%HERE%requirements.txt"
  if errorlevel 1 (
    echo.
    echo Dependency install failed. Connect to the internet and re-run frogs.bat
    pause
    exit /b 1
  )
  echo installed> "%SENTINEL%"
)

"%PY%" -m frogs_yt %*
