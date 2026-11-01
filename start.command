#!/bin/bash
# Double-click this file to start AI Video Maker.
cd "$(dirname "$0")" || exit 1
clear 2>/dev/null
echo "🎬  AI Video Maker"
echo "------------------"

# 1. Make sure Python 3 is installed
if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >/dev/null 2>&1; then
  echo ""
  echo "Python 3 isn't installed yet (or it's too old)."
  echo ""
  echo "If a box popped up asking to install 'command line developer tools', click Install,"
  echo "wait for it to finish, then double-click start.command again."
  echo ""
  echo "Otherwise, I'm opening the Python download page. Download the macOS installer,"
  echo "run it, then double-click start.command again."
  open "https://www.python.org/downloads/macos/"
  echo ""
  read -r -p "Press Enter to close this window..."
  exit 1
fi
echo "✔ Found $(python3 --version)"

# 2. First run: create a private environment and install the tools (takes a minute or two, once)
if [ ! -d ".venv" ]; then
  echo "Setting things up for the first time..."
  python3 -m venv .venv || { echo "Couldn't create the environment."; read -r -p "Press Enter..."; exit 1; }
fi
# shellcheck disable=SC1091
source .venv/bin/activate
if [ ! -f ".venv/.installed" ] || [ requirements.txt -nt ".venv/.installed" ]; then
  echo "Installing the video tools (only happens once)..."
  python -m pip install --upgrade pip -q
  if python -m pip install -r requirements.txt; then
    touch .venv/.installed
  else
    echo ""
    echo "Installing failed. Check your internet connection and try again."
    read -r -p "Press Enter to close this window..."
    exit 1
  fi
fi
echo "✔ Tools ready"
if [ "$1" = "--setup-only" ]; then exit 0; fi

# 3. Start the app and open it in the browser
echo ""
echo "Opening http://localhost:8501 in your browser..."
echo "Keep this window open while you use the app. Close it (or press Ctrl+C) to stop."
( sleep 3; open "http://localhost:8501" ) &
python -m streamlit run app.py
