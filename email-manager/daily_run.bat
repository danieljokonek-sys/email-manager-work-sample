@echo off
REM Email Cleanup ^& Brief Manager — for Windows Task Scheduler
REM Runs the full pipeline: fetch, analyze, digest, cleanup
REM Skips if not set up yet (no .env file)
REM Logs stdout+stderr to logs\daily_run.log (rolling, single file).

cd /d "%~dp0"

if not exist "logs" mkdir "logs"
set LOG=logs\daily_run.log

echo. >> "%LOG%"
echo ============================================== >> "%LOG%"
echo Run started: %DATE% %TIME% >> "%LOG%"
echo CWD: %CD% >> "%LOG%"
echo ============================================== >> "%LOG%"

if not exist ".env" (
    echo Email Manager not configured yet. Run run.bat first to set up. >> "%LOG%" 2>&1
    exit /b 1
)

if not exist ".venv\Scripts\activate.bat" (
    python -m venv .venv >> "%LOG%" 2>&1
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt >> "%LOG%" 2>&1
) else (
    call .venv\Scripts\activate.bat
)

python main.py run >> "%LOG%" 2>&1
set EXITCODE=%ERRORLEVEL%

echo Run finished: %DATE% %TIME%  exit=%EXITCODE% >> "%LOG%"
exit /b %EXITCODE%
