@echo off
cd /d "%~dp0"
py -3.11 -m pip install -r requirements.txt pyinstaller
py -3.11 -m PyInstaller --onefile --noconsole --name ShisaPoint --clean --icon icon\ShisaPoint.ico --add-data "icon\*.ico;icon" --collect-submodules winrt desktop_lens.py
