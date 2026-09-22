@echo off
REM Email Manager: quick launcher for Windows.
REM First run: creates a virtual environment, installs the package, opens the setup wizard.
REM After setup: runs the full daily pipeline.
REM To re-run setup later: python setup_wizard.py

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo Setting up the virtual environment for the first time...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -e .
) else (
    call .venv\Scripts\activate.bat
)

if not exist ".env" (
    echo.
    echo First-time setup: opening the setup wizard...
    echo.
    python setup_wizard.py
    if not exist ".env" (
        echo Setup was not completed. Run this script again after completing setup.
        pause
        exit /b 1
    )
)

if not exist "config.yaml" (
    echo.
    echo No config.yaml found: opening the setup wizard...
    echo.
    python setup_wizard.py
)

python main.py run
pause
