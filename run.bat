@echo off
REM 启动滑块自动登录（托盘常驻）
REM 优先用本机 Python 3.14（只有它带 tkinter，托盘设置界面需要），
REM 找不到就退回 PATH 里的 pythonw。
chcp 65001 >nul
setlocal
set "PY=%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe"
if not exist "%PY%" set "PY=pythonw.exe"
cd /d "%~dp0src"
start "" "%PY%" main.py
endlocal
