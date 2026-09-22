#!/usr/bin/env bash
# Email Manager: quick launcher for Mac/Linux.
# First run: creates a virtual environment, installs the package, opens the setup wizard.
# After setup: runs the full daily pipeline.

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "Setting up the virtual environment for the first time..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -e .
else
    source .venv/bin/activate
fi

if [ ! -f ".env" ]; then
    echo ""
    echo "First-time setup: opening the setup wizard..."
    echo ""
    python3 setup_wizard.py
    if [ ! -f ".env" ]; then
        echo "Setup was not completed. Run this script again after completing setup."
        exit 1
    fi
fi

if [ ! -f "config.yaml" ]; then
    echo "No config.yaml found: opening the setup wizard..."
    python3 setup_wizard.py
fi

python3 main.py run
