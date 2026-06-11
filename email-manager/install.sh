#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Email Cleanup & Brief Manager — Mac Installer
# Double-click this file or run: bash install.sh
# ─────────────────────────────────────────────────────────────────────────────
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo ""
echo "================================================"
echo "  Email Cleanup & Brief Manager — Installer"
echo "================================================"
echo ""

# ── 1. Check macOS ───────────────────────────────────────────────────────────
if [[ "$OSTYPE" != "darwin"* ]]; then
    echo "ERROR: This installer is for Mac only."
    exit 1
fi

# ── 2. Check / install Homebrew ──────────────────────────────────────────────
if ! command -v brew &> /dev/null; then
    echo "Installing Homebrew (Mac package manager)..."
    echo "You may be asked for your Mac password."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for Apple Silicon Macs
    if [[ -f "/opt/homebrew/bin/brew" ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    echo "✓ Homebrew installed"
else
    echo "✓ Homebrew already installed"
fi

# ── 3. Check / install Python 3.11+ ─────────────────────────────────────────
PYTHON=""
for cmd in python3.13 python3.12 python3.11 python3; do
    if command -v "$cmd" &> /dev/null; then
        VER=$("$cmd" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo "0")
        MAJ=$("$cmd" -c 'import sys; print(sys.version_info.major)' 2>/dev/null || echo "0")
        if [[ "$MAJ" -ge 3 && "$VER" -ge 11 ]]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    echo "Installing Python 3.11..."
    brew install python@3.11
    PYTHON="python3.11"
    echo "✓ Python installed"
else
    echo "✓ Python found: $PYTHON"
fi

# ── 4. Check tkinter (needed for setup wizard) ───────────────────────────────
if ! $PYTHON -c "import tkinter" &> /dev/null; then
    echo "Installing tkinter..."
    brew install python-tk@3.11 2>/dev/null || brew install python-tk 2>/dev/null || true
fi

# ── 5. Install Python packages ───────────────────────────────────────────────
echo ""
echo "Installing required packages (this may take a minute)..."
$PYTHON -m pip install -r "$DIR/requirements.txt" --quiet --upgrade
echo "✓ Packages installed"

# ── 6. Create required directories ───────────────────────────────────────────
mkdir -p "$DIR/credentials"
mkdir -p "$DIR/data"
echo "✓ Directories ready"

# ── 7. Launch setup wizard ───────────────────────────────────────────────────
echo ""
echo "Launching setup wizard..."
echo ""
$PYTHON "$DIR/setup_wizard.py" --app-dir "$DIR" --python "$PYTHON"

echo ""
echo "Installation complete!"
