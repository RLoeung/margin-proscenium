@echo off
setlocal
title Kokoro TTS Server Launcher

set "ROOT=C:\AI\kokoro"
set "PYTHON=C:\AI\kokoro\.venv\Scripts\python.exe"
set "CURRENT_MODULE=scripts.server:app"
set "NARRATION_MODULE=scripts.server_v0_11:app"
set "HOST=0.0.0.0"
set "PORT=8282"

:menu
cls
echo ============================================================
echo                  KOKORO TTS SERVER
echo ============================================================
echo.
echo   1. Current development server
echo      Module: %CURRENT_MODULE%
echo.
echo   2. v0_11 narration-only server
echo      Module: %NARRATION_MODULE%
echo.
echo   Q. Quit
echo.
set /p "choice=Choose an option [1/2/Q]: "

if /I "%choice%"=="1" goto current
if /I "%choice%"=="2" goto narration
if /I "%choice%"=="Q" goto end

echo.
echo Invalid choice.
timeout /t 2 >nul
goto menu

:current
call :check_python
if errorlevel 1 goto failed

cls
echo Starting current development TTS server...
echo.
echo Module: %CURRENT_MODULE%
echo Host:   %HOST%
echo Port:   %PORT%
echo Python: %PYTHON%
echo.
cd /d "%ROOT%"
"%PYTHON%" -m uvicorn %CURRENT_MODULE% --host %HOST% --port %PORT%
goto finished

:narration
call :check_python
if errorlevel 1 goto failed

if not exist "%ROOT%\scripts\server_v0_11.py" (
    echo.
    echo ERROR: Narration server module was not found:
    echo   %ROOT%\scripts\server_v0_11.py
    echo.
    echo The underscore filename is intentional because Uvicorn imports
    echo this file as the Python module "scripts.server_v0_11".
    goto failed
)

cls
echo Starting v0_11 narration-only TTS server...
echo.
echo Module: %NARRATION_MODULE%
echo Host:   %HOST%
echo Port:   %PORT%
echo Python: %PYTHON%
echo.
cd /d "%ROOT%"
"%PYTHON%" -m uvicorn %NARRATION_MODULE% --host %HOST% --port %PORT%
goto finished

:check_python
if exist "%PYTHON%" exit /b 0
echo.
echo ERROR: Kokoro virtual-environment Python was not found:
echo   %PYTHON%
exit /b 1

:failed
echo.
echo The server could not be started.
pause
goto menu

:finished
echo.
echo ============================================================
echo The server process has exited.
echo ============================================================
echo.
pause
goto menu

:end
endlocal
exit /b
