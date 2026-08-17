@echo off
chcp 65001 >nul
setlocal

set "ROOT_DIR=%~dp0"
set "PYTHONPATH=%ROOT_DIR%src;%PYTHONPATH%"

where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3 -m tga_analyzer
) else (
    python -m tga_analyzer
)

if %ERRORLEVEL% neq 0 (
    echo.
    echo GraphMaker Python 3.9互換版の起動に失敗しました。上記のエラー内容を確認してください。
    echo 必要環境: Python 3.9以上、openpyxl 3.1.5
    pause
)

endlocal
exit /b
