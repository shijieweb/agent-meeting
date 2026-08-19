@echo off
cd /d "C:\Users\67972\WorkBuddy\workbuddy\agent-meeting\server"
"C:\Users\67972\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8011
