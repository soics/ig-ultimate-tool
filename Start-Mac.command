#!/bin/bash
# IG Ultimate Tool - double-click to start on macOS (first time: right-click > Open).
# First run only: installs "uv" from astral.sh (the official installer), which downloads Python for you.
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "First run: setting things up. This only happens once and takes a minute or two."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "Setup failed. Check your internet connection, then open this file again."
  read -n 1 -s -r -p "Press any key to close..."
  exit 1
fi
uv run --quiet --locked ig-ultimate "$@"
echo
read -n 1 -s -r -p "Press any key to close this window..."
