#!/bin/bash
# Build citect-tracker standalone executable for Linux
# Requirements: Python 3.10+

set -e

echo "=== Citect Record Tracker - Linux Build ==="
echo

# Create venv if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# Install dependencies
echo "Installing dependencies..."
.venv/bin/pip install -e .[build] --quiet

# Build
echo
echo "Building executable..."
.venv/bin/pyinstaller citect-tracker.spec --noconfirm

echo
if [ -f "dist/citect-tracker" ]; then
    echo "Build successful!"
    echo "Output: dist/citect-tracker"
    echo "Size: $(du -h dist/citect-tracker | cut -f1)"
else
    echo "Build FAILED. Check output above for errors."
    exit 1
fi
