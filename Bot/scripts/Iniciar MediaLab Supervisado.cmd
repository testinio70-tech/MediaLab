@echo off
chcp 65001 >nul
title MediaLab Supervisor
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_medialab_supervisor.ps1"
echo.
echo MediaLab Supervisor se ha detenido.
pause
