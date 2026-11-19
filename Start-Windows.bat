@echo off
rem IG Ultimate Tool - double-click to start on Windows.
rem First run only: installs "uv" from astral.sh (the official installer), which downloads Python for you.
setlocal
cd /d "%~dp0"
title IG Ultimate Tool
set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
where uv >nul 2>nul
if errorlevel 1 (
  echo First run: setting things up. This only happens once and takes a minute or two.
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
)
where uv >nul 2>nul
if errorlevel 1 (
  echo.
  echo Setup failed. Check your internet connection, then double-click this file again.
  pause
  exit /b 1
)
uv run --quiet ig-ultimate %*
echo.
pause
