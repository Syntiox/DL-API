#!/bin/bash
# startup.sh — Start bgutil PO token server in background, then start main API

set -e

echo "[STARTUP] Starting bgutil PO Token server on port 4416..."
cd /app/pot_server/bgutil/server
node build/main.js &
BGUTIL_PID=$!
echo "[STARTUP] bgutil started (PID: $BGUTIL_PID)"

# Wait for bgutil to be ready (max 15 seconds)
echo "[STARTUP] Waiting for bgutil to be ready..."
for i in $(seq 1 15); do
    if curl -sf http://localhost:4416/ > /dev/null 2>&1; then
        echo "[STARTUP] bgutil is ready ✅"
        break
    fi
    if [ $i -eq 15 ]; then
        echo "[STARTUP] ⚠️  bgutil did not start in time — continuing without PO tokens"
    fi
    sleep 1
done

echo "[STARTUP] Starting Syntiox DL API (uvicorn)..."
cd /app
exec uvicorn app:app --host 0.0.0.0 --port 8000
