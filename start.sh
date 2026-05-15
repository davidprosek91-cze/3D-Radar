#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
echo "=== 3D Radar System ==="

PYTHON=""
for p in "$DIR/venv/bin/python3" python3 python; do
    if command -v "$p" &>/dev/null; then PYTHON="$p"; break; fi
done
if [ -z "$PYTHON" ]; then echo "Python nenalezen!"; exit 1; fi

echo "Spoustim SDR server (python: $PYTHON)..."
"$PYTHON" "$DIR/sdr_server.py" &
SDR_PID=$!

echo "Spoustim HTTP server na portu 8080..."
python3 -m http.server 8080 -d "$DIR" &
HTTP_PID=$!

echo ""
echo "3D Radar pripraven!"
echo "  Frontend: http://localhost:8080"
echo "  SDR: ws://localhost:8765"
echo "  Stop: Ctrl+C"
echo ""

cleanup() { kill $SDR_PID $HTTP_PID 2>/dev/null; exit 0; }
trap cleanup SIGINT SIGTERM
wait
