@echo off
chcp 65001 > nul
cd /d "%~dp0"
python register_menu.py
