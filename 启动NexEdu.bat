@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动 山海智绘·NexEdu ...
py main.py
pause