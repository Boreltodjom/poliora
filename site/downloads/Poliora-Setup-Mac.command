#!/usr/bin/env bash
set -euo pipefail

PRODUCT_NAME="Poliora"
PACKAGE_NAME="poliora"
PACKAGE_SOURCE="${POLIORA_PACKAGE_SOURCE:-$PACKAGE_NAME}"
INSTALL_ROOT="$HOME/Library/Application Support/Poliora"
VENV="$INSTALL_ROOT/runtime"

echo ""
echo "================================================================"
echo "  $PRODUCT_NAME local workspace setup"
echo "================================================================"
echo ""
echo "This installs a private Python environment in:"
echo "  $VENV"
echo ""
echo "$PRODUCT_NAME does not upload prompts, source code, or usage data."
echo ""

PYTHON_BIN=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3 python; do
  if command -v "$candidate" &>/dev/null && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
    PYTHON_BIN="$candidate"
    break
  fi
done
if [[ -z "$PYTHON_BIN" ]]; then
  echo "Python 3.11 or later is required before setup can continue."
  echo "Install Python via Homebrew (brew install python) or from https://www.python.org/downloads/"
  exit 1
fi

echo "[1/4] Creating the private runtime..."
mkdir -p "$INSTALL_ROOT"
"$PYTHON_BIN" -m venv "$VENV"

echo "[2/4] Updating the installer tools..."
"$VENV/bin/python" -m pip install --quiet --upgrade pip

echo "[3/4] Installing $PRODUCT_NAME..."
if ! "$VENV/bin/python" -m pip install --quiet --upgrade "$PACKAGE_SOURCE"; then
  echo ""
  echo "$PRODUCT_NAME could not be downloaded from PyPI."
  echo "Check your connection, then run this file again."
  exit 1
fi

echo "[4/4] Checking supported local AI tools..."
"$VENV/bin/python" -m poliora.main scan

echo ""
echo "================================================================"
echo "  $PRODUCT_NAME setup finished."
echo "================================================================"
echo "Starting the local Poliora dashboard now..."
if [[ "${POLIORA_NO_LAUNCH:-}" == "1" ]]; then
  exit 0
fi
nohup "$VENV/bin/python" -m poliora.app_launcher > "$INSTALL_ROOT/poliora.log" 2>&1 &
echo "The dashboard will open in your browser. Logs: $INSTALL_ROOT/poliora.log"
echo ""
