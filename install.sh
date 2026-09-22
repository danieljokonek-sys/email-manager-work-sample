#!/bin/bash
# Email Manager: Mac installer. Double-click this file or run: bash install.sh
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo ""
echo "================================"
echo "  Email Manager: installer"
echo "================================"
echo ""

if [[ "$OSTYPE" != "darwin"* ]]; then
    echo "ERROR: This installer is for Mac only. On Linux, run: bash run.sh"
    exit 1
fi

# Homebrew
if ! command -v brew &> /dev/null; then
    echo "Installing Homebrew (you may be asked for your Mac password)..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    if [[ -f "/opt/homebrew/bin/brew" ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
fi
echo "Homebrew: ok"

# Python 3.11+
PYTHON=""
for cmd in python3.13 python3.12 python3.11 python3; do
    if command -v "$cmd" &> /dev/null; then
        if "$cmd" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            PYTHON="$cmd"
            break
        fi
    fi
done
if [[ -z "$PYTHON" ]]; then
    echo "Installing Python 3.12..."
    brew install python@3.12
    PYTHON="python3.12"
fi
echo "Python: $PYTHON"

# tkinter for the setup wizard
if ! $PYTHON -c "import tkinter" &> /dev/null; then
    echo "Installing tkinter..."
    brew install python-tk@3.12 2>/dev/null || brew install python-tk 2>/dev/null || true
fi

# Virtual environment + package
if [ ! -d "$DIR/.venv" ]; then
    $PYTHON -m venv "$DIR/.venv"
fi
source "$DIR/.venv/bin/activate"
pip install -q --upgrade pip
pip install -q -e "$DIR"
echo "Packages: ok"

mkdir -p "$DIR/credentials" "$DIR/data" "$DIR/logs"

echo ""
echo "Opening the setup wizard..."
python "$DIR/setup_wizard.py" --app-dir "$DIR" --python "$DIR/.venv/bin/python"

echo ""
echo "Installation complete."
