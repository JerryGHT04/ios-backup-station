#!/bin/bash
# Start usbmuxd2 (kept alive by a loop), then the web app if present.
set -u

mkdir -p /var/lib/lockdown /var/run

run_usbmuxd() {
  while true; do
    echo "[entrypoint] starting usbmuxd2"
    # usbmuxd2 runs in the foreground by default. Wi-Fi discovery uses the host avahi via /run/avahi-daemon/socket.
    usbmuxd ${USBMUXD_ARGS:--v} || true
    echo "[entrypoint] usbmuxd2 exited, restarting in 3s"
    sleep 3
  done
}

run_usbmuxd &

if [ -f /app/server.py ]; then
  exec python3 -u /app/server.py
fi

echo "[entrypoint] no web app yet, idling (usbmuxd2 running)"
exec sleep infinity
