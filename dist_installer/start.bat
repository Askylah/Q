@echo off
title Q Server
color 0b

echo ========================================================
echo                 Q - IGNITION SEQUENCE
echo ========================================================
echo.

echo [1/3] Checking Python Environment...
if not exist ".venv" (
    echo Creating isolated virtual environment...
    py -m venv .venv
)

echo Activating environment and verifying dependencies...
call .venv\Scripts\activate
py -m pip install -r requirements.txt -q

echo.
echo [2/3] Verifying Docker Security Sandbox...
docker info >nul 2>&1
if %errorlevel% neq 0 (
    color 0c
    echo.
    echo [CRITICAL ERROR] Docker Desktop is not running or not installed!
    echo The security sandbox requires Docker. Please install Docker Desktop:
    echo https://www.docker.com/products/docker-desktop/
    echo Install it, start it, and run this file again.
    pause
    exit /b
)

echo Rebuilding Padded Room image (if necessary)...
docker build -t padded_room -f Dockerfile.padded_room . -q

echo.
echo [3/3] Igniting Unified Server...
echo The app will open in your browser automatically.
start http://127.0.0.1:8000
py -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
