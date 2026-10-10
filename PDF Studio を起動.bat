@echo off
chcp 65001 >nul
title PDF Studio
cd /d "%~dp0webapp"

echo.
echo   PDF Studio をこのPCの中で起動しています...
echo.
echo   このウィンドウは閉じないでください。
echo   閉じると PDF Studio も終了します。
echo.
echo   （インターネット上の公開版を使う場合、この起動は不要です）
echo.

start "" http://127.0.0.1:8000/
python -m http.server 8000 --bind 127.0.0.1

echo.
echo   PDF Studio を終了しました。
pause
