@echo off
REM Email Manager: entry point for Windows Task Scheduler.
REM Runs the full pipeline (fetch, analyze, digest, cleanup) and appends
REM stdout+stderr to logs\daily_run.log. The app also keeps its own
REM logs\email_manager.log with per-call Claude usage.
REM
REM Once-per-day guard: after a successful run, today's date is written to
REM logs\last_success.txt and any further launch the same day exits at once.
REM This stops duplicate digests when more than one trigger fires (a second
REM scheduled task, logon triggers, a reboot). Pass --force to override.

cd /d "%~dp0"

if not exist "logs" mkdir "logs"
set LOG=logs\daily_run.log
set MARKER=logs\last_success.txt

if /I "%~1"=="--force" goto run
if not exist "%MARKER%" goto run
set /p LASTDATE=<"%MARKER%"
if not "%LASTDATE%"=="%DATE%" goto run
echo Skipped: already ran successfully today (%DATE% %TIME%). Use --force to run again. >> "%LOG%"
exit /b 0

:run
echo. >> "%LOG%"
echo ============================================== >> "%LOG%"
echo Run started: %DATE% %TIME% >> "%LOG%"
echo ============================================== >> "%LOG%"

if not exist ".env" (
    echo Email Manager is not configured yet. Run run.bat first. >> "%LOG%" 2>&1
    exit /b 1
)

if not exist ".venv\Scripts\activate.bat" (
    python -m venv .venv >> "%LOG%" 2>&1
    call .venv\Scripts\activate.bat
    pip install -e . >> "%LOG%" 2>&1
) else (
    call .venv\Scripts\activate.bat
)

python main.py run >> "%LOG%" 2>&1
set EXITCODE=%ERRORLEVEL%

if "%EXITCODE%"=="0" >"%MARKER%" echo %DATE%

echo Run finished: %DATE% %TIME%  exit=%EXITCODE% >> "%LOG%"
exit /b %EXITCODE%
