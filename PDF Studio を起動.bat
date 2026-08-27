@echo off
chcp 65001 >nul
title PDF Studio
cd /d "%~dp0"

echo.
echo   PDF Studio を起動しています...
echo.
echo   このウィンドウは閉じないでください。
echo   閉じると PDF Studio も終了します。
echo.

start "" http://127.0.0.1:8000/
python -m uvicorn backend.main:app --port 8000 --host 127.0.0.1

echo.
echo   PDF Studio を終了しました。
pause
