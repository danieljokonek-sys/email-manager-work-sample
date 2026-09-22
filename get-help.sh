#!/usr/bin/env bash
# Email Cleanup & Brief Manager — Troubleshooter
# Runs diagnostics, copies results to clipboard, and opens Claude AI for help.

cd "$(dirname "$0")"
echo ""
echo "========================================"
echo "  Email Manager — Troubleshooter"
echo "========================================"
echo ""
echo "Running diagnostics..."
echo ""

REPORT_FILE="/tmp/email-manager-diagnostics.txt"

{
    echo "=== Email Cleanup & Brief Manager — Diagnostic Report ==="
    echo "Generated: $(date)"
    echo ""

    echo "--- System ---"
    echo "OS: $(uname -s) $(uname -r)"
    echo "Arch: $(uname -m)"
    echo ""

    echo "--- Python ---"
    python3 --version 2>&1
    echo "Path: $(which python3 2>&1)"
    echo ""

    echo "--- Dependencies ---"
    python3 -c "import anthropic; print(f'anthropic: {anthropic.__version__}')" 2>&1
    python3 -c "import click; print(f'click: {click.__version__}')" 2>&1
    python3 -c "import rich; print(f'rich: {rich.__version__}')" 2>&1
    python3 -c "import yaml; print('pyyaml: OK')" 2>&1
    python3 -c "import msal; print(f'msal: {msal.__version__}')" 2>&1
    python3 -c "import google.auth; print('google-auth: OK')" 2>&1
    echo ""

    echo "--- Config ---"
    [ -f ".env" ] && echo ".env: EXISTS" || echo ".env: MISSING"
    [ -f "config.yaml" ] && echo "config.yaml: EXISTS" || echo "config.yaml: MISSING"
    [ -f "credentials/credentials.json" ] && echo "credentials.json: EXISTS" || echo "credentials.json: MISSING"
    echo ""

    echo "--- Token Files ---"
    ls credentials/token_*.json 2>/dev/null || echo "No token files found"
    echo ""

    echo "--- Database ---"
    [ -f "data/tracker.db" ] && echo "tracker.db: EXISTS" || echo "tracker.db: MISSING"
    [ -f "data/organized.db" ] && echo "organized.db: EXISTS" || echo "organized.db: MISSING"
    echo ""

    echo "--- Virtual Environment ---"
    [ -d ".venv" ] && echo ".venv: EXISTS" || echo ".venv: MISSING"
    echo ""

    echo "--- Recent Errors ---"
    echo "Running: python3 main.py status"
    python3 main.py status 2>&1
    echo ""

    echo "=== End of Diagnostic Report ==="
    echo ""
    echo "Paste this report into Claude AI for help: https://claude.ai"
} > "$REPORT_FILE" 2>&1

# Show results
cat "$REPORT_FILE"

# Copy to clipboard
if command -v pbcopy &> /dev/null; then
    cat "$REPORT_FILE" | pbcopy
    echo ""
    echo "Diagnostic report copied to clipboard! (pbcopy)"
elif command -v xclip &> /dev/null; then
    cat "$REPORT_FILE" | xclip -selection clipboard
    echo ""
    echo "Diagnostic report copied to clipboard! (xclip)"
else
    echo ""
    echo "Could not copy to clipboard automatically."
    echo "Report saved to: $REPORT_FILE"
    echo "Copy it manually and paste into Claude AI."
fi

echo ""
echo "========================================"
echo "  Opening Claude AI in your browser..."
echo "  Paste the report and describe your issue."
echo "========================================"
echo ""

# Open Claude in browser
if command -v open &> /dev/null; then
    open "https://claude.ai"
elif command -v xdg-open &> /dev/null; then
    xdg-open "https://claude.ai"
fi
