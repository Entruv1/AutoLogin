@echo off
REM 打包成单文件 exe（含 YOLOv8-seg 滑块模型）
REM 必须用系统 Python 3.14：只有它带 tkinter，且依赖齐全。
setlocal enabledelayedexpansion
chcp 65001 >nul

set "PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not exist "%PY%" set "PY=python.exe"
set "ROOT=%~dp0"
set "SRC=%ROOT%src"
set "DIST=%ROOT%dist"
set "WORK=%TEMP%\autologin_vision_build"
set "ICON=%ROOT%assets\app.ico"

echo === 1) 生成图标 ===
if not exist "%ROOT%assets" mkdir "%ROOT%assets"
"%PY%" -c "import sys; sys.path.insert(0,r'%SRC%'); import tray; tray.save_ico(r'%ICON%'); print('icon ok')"

echo === 2) 清理旧产物 ===
if exist "%DIST%\auto_login_vision.exe" ren "%DIST%\auto_login_vision.exe" auto_login_vision.exe.old
if not exist "%DIST%" mkdir "%DIST%"

echo === 3) PyInstaller 单文件打包 ===
"%PY%" -m PyInstaller --noconfirm --onefile --windowed ^
    --name auto_login_vision ^
    --icon "%ICON%" ^
    --paths "%SRC%" ^
    --add-data "%ROOT%captcha_recognizer;captcha_recognizer" ^
    --collect-all onnxruntime ^
    --collect-all shapely ^
    --exclude-module matplotlib ^
    --exclude-module PyQt5 ^
    --exclude-module PySide6 ^
    --distpath "%DIST%" ^
    --workpath "%WORK%" ^
    "%SRC%\main.py"
if errorlevel 1 goto :failed

echo === 4) 清理 ===
if exist "%DIST%\auto_login_vision.exe.old" del /f /q "%DIST%\auto_login_vision.exe.old"

echo.
echo === 完成 ===
echo exe: %DIST%\auto_login_vision.exe
echo 首次运行会在 exe 同目录生成 config.json，把账号密码填进去即可。
endlocal
goto :eof

:failed
echo.
echo === 打包失败 ===
endlocal
exit /b 1
