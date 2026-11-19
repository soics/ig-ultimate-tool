#!/usr/bin/env bash
# IG Ultimate Tool - start on Linux:  ./start-linux.sh   (add --preview to try a pretend account)
# First run only: installs "uv" from astral.sh (the official installer), which downloads Python for you.
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "First run: setting things up. This only happens once and takes a minute or two."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
command -v uv >/dev/null 2>&1 || { echo "Setup failed. Check your internet connection and try again."; exit 1; }
exec uv run --quiet ig-ultimate "$@"
