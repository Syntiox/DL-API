#!/bin/bash
# startup.sh — Start bgutil PO token server in background, then start main API

set -e

echo "[STARTUP] Starting bgutil PO Token server on port 4416..."
cd /app/pot_server/bgutil/server
node build/main.js &
BGUTIL_PID=$!
echo "[STARTUP] bgutil started (PID: $BGUTIL_PID)"

# Wait for bgutil to be ready.
# bgutil binds to [::]:4416 (IPv6 wildcard). We check both IPv4 and IPv6.
# bgutil doesn't have a root "/" health endpoint — check if port is open instead.
echo "[STARTUP] Waiting for bgutil to be ready..."
BGUTIL_READY=0
for i in $(seq 1 20); do
    # Try curl with --ipv4 and --ipv6 fallback
    if curl -sf --max-time 2 http://127.0.0.1:4416/ > /dev/null 2>&1 || \
       curl -sf --max-time 2 http://[::1]:4416/ > /dev/null 2>&1 || \
       (command -v nc > /dev/null && nc -z 127.0.0.1 4416 > /dev/null 2>&1) || \
       (command -v nc > /dev/null && nc -z ::1 4416 > /dev/null 2>&1); then
        BGUTIL_READY=1
        echo "[STARTUP] bgutil is ready ✅ (after ${i}s)"
        break
    fi
    sleep 1
done

if [ $BGUTIL_READY -eq 0 ]; then
    echo "[STARTUP] ⚠️  bgutil did not respond to health check — but may still be running"
    echo "[STARTUP] Continuing startup (bgutil may still work via IPv6 binding [::]:4416)"
fi

echo "[STARTUP] Starting Syntiox DL API (uvicorn)..."
cd /app
exec uvicorn app:app --host 0.0.0.0 --port 8000
