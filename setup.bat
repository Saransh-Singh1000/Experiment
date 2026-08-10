@echo off
cd .venv\Scripts
call activate.bat
pip install ursina; uuid; sounddevice; numpy; websocket-client
deactivate
cd ../..