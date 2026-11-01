#!/bin/bash
# Starts AI Video Maker in the background (no Terminal window) and opens it in your browser.
# Used by "AI Video Maker.app" and by Cmd+Shift+B in VS Code. Safe to run again: if the app is
# already running it just opens the browser.

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/Current/bin:$PATH"
PORT=8501
URL="http://localhost:$PORT"
mkdir -p logs

notify() { osascript -e "display notification \"$1\" with title \"AI Video Maker\"" >/dev/null 2>&1; }
alert() {
  osascript -e "display dialog \"$1\" with title \"AI Video Maker\" buttons {\"OK\"} default button 1 with icon caution" \
    >/dev/null 2>&1 || echo "$1"
}
running() { curl -s -m 2 "$URL/_stcore/health" 2>/dev/null | grep -q ok; }

# Already running? Just show it.
if running; then
  open "$URL"
  exit 0
fi

# Python is needed once, to set things up.
if [ ! -x ".venv/bin/python" ] && ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >/dev/null 2>&1; then
  alert "Python 3 isn't installed yet. I'll open the download page: install the macOS version, then open AI Video Maker again."
  open "https://www.python.org/downloads/macos/"
  exit 1
fi

notify "Starting up… (the first time takes a minute or two)"
if ! bash start.command --setup-only > logs/setup.log 2>&1; then
  alert "Setting up the video tools failed. Check your internet connection and try again. Details are in logs/setup.log."
  open -e logs/setup.log
  exit 1
fi

# Start the app in the background. It keeps running after this script ends.
nohup .venv/bin/python -m streamlit run app.py --server.port "$PORT" > logs/app.log 2>&1 &
disown

for _ in $(seq 1 90); do
  running && break
  sleep 1
done

if running; then
  open "$URL"
else
  alert "The app didn't start. Details are in logs/app.log."
  open -e logs/app.log
  exit 1
fi
