#!/usr/bin/env bash
# Email Cleanup & Brief Manager — Quick launcher for Mac/Linux
# On first run: sets up venv and launches the setup wizard
# After setup: runs the full pipeline

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "Setting up virtual environment for the first time..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
else
    source .venv/bin/activate
fi

# First-run detection: launch wizard if no .env
if [ ! -f ".env" ]; then
    echo ""
    echo "First-time setup detected — launching setup wizard..."
    echo ""
    python3 setup_wizard.py
    if [ ! -f ".env" ]; then
        echo "Setup was not completed. Run this script again after completing setup."
        exit 1
    fi
fi

# Copy template config if needed
if [ ! -f "config.yaml" ] && [ -f "config.template.yaml" ]; then
    cp config.template.yaml config.yaml
    echo "No config.yaml found — launching setup wizard..."
    python3 setup_wizard.py
fi

python3 main.py run
