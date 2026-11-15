@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Project virtual environment was not found.
    echo Create it with: py -3.13 -m venv .venv
    pause
    exit /b 1
)

set "STREAMLIT_BROWSER_GATHER_USAGE_STATS=false"
set "STREAMLIT_SERVER_SHOW_EMAIL_PROMPT=false"
echo Starting Retail Agent Project...
echo Local page: http://127.0.0.1:8501
echo Keep this window open while using the page.
".venv\Scripts\python.exe" -m streamlit run app.py --server.address 127.0.0.1 --server.headless false
set "STARTUP_ERROR=%ERRORLEVEL%"

if not "%STARTUP_ERROR%"=="0" (
    echo.
    echo ERROR: Streamlit exited with code %STARTUP_ERROR%.
    pause
    exit /b %STARTUP_ERROR%
)

endlocal
