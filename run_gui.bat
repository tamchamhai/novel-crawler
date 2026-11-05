@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo Dang khoi dong Novel Crawler GUI bang moi truong .venv (GPU RTX 5060)...
.\.venv\Scripts\python.exe gui.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Co loi xay ra khi chay GUI.
    pause
)

