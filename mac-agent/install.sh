#!/bin/bash
echo ""
echo "╔══════════════════════════════════════╗"
echo "║     SoundBridg — Mac Agent Setup     ║"
echo "╚══════════════════════════════════════╝"
echo ""

PYTHON="/usr/local/Cellar/python@3.11/3.11.15/Frameworks/Python.framework/Versions/3.11/bin/python3.11"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LAUNCHER="$HOME/Desktop/SoundBridg.command"

cat > "$LAUNCHER" <<LAUNCH
#!/bin/bash
$PYTHON $SCRIPT_DIR/soundbridg_agent.py
LAUNCH

chmod +x "$LAUNCHER"

echo "✅ Done! Double-click 'SoundBridg.command' on your Desktop to launch."
