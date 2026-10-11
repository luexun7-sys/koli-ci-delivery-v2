@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install -r requirements.txt || (echo Installation Python requise && pause && exit /b 1)
start "KOLI CI browser" cmd /c "timeout /t 3 >nul && start http://127.0.0.1:8000"
python -m uvicorn server:app --host 127.0.0.1 --port 8000
pause

