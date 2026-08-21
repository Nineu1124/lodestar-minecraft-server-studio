@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Ripple Server Panel

where python >nul 2>nul
if errorlevel 1 (
  echo 未找到 Python。请安装 Python 3.11 或更高版本，并勾选 Add Python to PATH。
  pause
  exit /b 1
)

python server.py
if errorlevel 1 (
  echo.
  echo 面板启动失败，请将上面的错误内容发给项目维护者。
  pause
)

