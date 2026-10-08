@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0dist_windows_gpu\anime-pic-manage.exe" (
  start "" "%~dp0dist_windows_gpu\anime-pic-manage.exe"
  exit /b 0
)
if exist "%~dp0dist_windows\anime-pic-manage.exe" (
  start "" "%~dp0dist_windows\anime-pic-manage.exe"
  exit /b 0
)
echo CharaForge desktop binary was not found.
echo Build the portable application or run pnpm desktop tauri:dev.
pause
exit /b 1
