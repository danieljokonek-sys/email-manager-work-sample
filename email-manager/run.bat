@echo off
REM Email Cleanup ^& Brief Manager — Quick launcher for Windows
REM First run: sets up venv and launches setup wizard
REM After setup: runs the full pipeline
REM To re-run setup: run "python setup_wizard.py" from this folder

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo Setting up virtual environment for the first time...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

REM First-run detection: launch wizard if no .env
if not exist ".env" (
    echo.
    echo First-time setup detected — launching setup wizard...
    echo.
    python setup_wizard.py
    if not exist ".env" (
        echo Setup was not completed. Run this script again after completing setup.
        pause
        exit /b 1
    )
)

if not exist "config.yaml" (
    if exist "config.template.yaml" (
        copy config.template.yaml config.yaml >nul
    )
    echo.
    echo No config.yaml found — launching setup wizard...
    echo.
    python setup_wizard.py
)

python main.py run
pause
