@echo off
REM Email Cleanup ^& Brief Manager — Troubleshooter
REM Runs diagnostics, copies results to clipboard, and opens Claude AI for help.

cd /d "%~dp0"
echo.
echo ========================================
echo   Email Manager — Troubleshooter
echo ========================================
echo.
echo Running diagnostics...
echo.

set "REPORT_FILE=%TEMP%\email-manager-diagnostics.txt"

(
    echo === Email Cleanup ^& Brief Manager — Diagnostic Report ===
    echo Generated: %date% %time%
    echo.

    echo --- System ---
    echo OS: Windows
    ver
    echo.

    echo --- Python ---
    python --version 2>&1
    echo Path:
    where python 2>&1
    echo.

    echo --- Dependencies ---
    python -c "import anthropic; print(f'anthropic: {anthropic.__version__}')" 2>&1
    python -c "import click; print(f'click: {click.__version__}')" 2>&1
    python -c "import rich; print(f'rich: {rich.__version__}')" 2>&1
    python -c "import yaml; print(f'pyyaml: OK')" 2>&1
    python -c "import msal; print(f'msal: {msal.__version__}')" 2>&1
    python -c "import google.auth; print(f'google-auth: OK')" 2>&1
    echo.

    echo --- Config ---
    if exist ".env" (echo .env: EXISTS) else (echo .env: MISSING)
    if exist "config.yaml" (echo config.yaml: EXISTS) else (echo config.yaml: MISSING)
    if exist "credentials\credentials.json" (echo credentials.json: EXISTS) else (echo credentials.json: MISSING)
    echo.

    echo --- Token Files ---
    if exist "credentials" (dir /b credentials\token_*.json 2>nul || echo No token files found) else (echo credentials folder: MISSING)
    echo.

    echo --- Database ---
    if exist "data\tracker.db" (echo tracker.db: EXISTS) else (echo tracker.db: MISSING)
    if exist "data\organized.db" (echo organized.db: EXISTS) else (echo organized.db: MISSING)
    echo.

    echo --- Virtual Environment ---
    if exist ".venv\Scripts\activate.bat" (echo .venv: EXISTS) else (echo .venv: MISSING)
    echo.

    echo --- Recent Errors ---
    echo Running: python main.py status
    python main.py status 2>&1
    echo.

    echo === End of Diagnostic Report ===
    echo.
    echo Paste this report into Claude AI for help: https://claude.ai
) > "%REPORT_FILE%" 2>&1

REM Show results
type "%REPORT_FILE%"

REM Copy to clipboard
type "%REPORT_FILE%" | clip
echo.
echo ========================================
echo   Diagnostic report copied to clipboard!
echo ========================================
echo.
echo Opening Claude AI in your browser...
echo Paste the report (Ctrl+V) and describe your issue.
echo.
start https://claude.ai
pause
