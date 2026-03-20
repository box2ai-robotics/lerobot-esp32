@echo off
REM Box2Driver Virtual Servo Bridge - Windows Launcher
REM Double-click to start, or run from command line.
REM
REM Prerequisites:
REM   pip install pyserial websockets
REM   com0com installed (https://sourceforge.net/projects/com0com/)
REM
REM Usage:
REM   start_servo_bridge.bat                    -- Full auto
REM   start_servo_bridge.bat -v                 -- Verbose
REM   start_servo_bridge.bat --ws ws://host:8765  -- Custom gateway

cd /d "%~dp0"
python -u virtual_servo_bridge.py %*
if errorlevel 1 (
    echo.
    echo Failed to start. Check prerequisites:
    echo   pip install pyserial websockets
    echo   Install com0com for virtual serial ports
    echo.
    pause
)
