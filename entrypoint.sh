#!/bin/bash
# Start usbmuxd2 (kept alive by a loop), then the web app if present.
set -u

mkdir -p /var/lib/lockdown /var/run

run_usbmuxd() {
  while true; do
    if [ "${USBMUXD_IMPLEMENTATION:-usbmuxd2}" = stock ]; then
      echo "[entrypoint] starting standard USB transport"
      /opt/usbmuxd-stock/sbin/usbmuxd --foreground --user root --config-dir /var/lib/lockdown || true
    else
      echo "[entrypoint] starting usbmuxd2"
      # usbmuxd2 adds Wi-Fi discovery through the host avahi socket.
      usbmuxd ${USBMUXD_ARGS:--v} || true
    fi
    echo "[entrypoint] USB transport exited, restarting in 3s"
    sleep 3
  done
}

run_usbmuxd &

if [ -f /app/server.py ]; then
  exec python3 -u /app/server.py
fi

echo "[entrypoint] no web app yet, idling (usbmuxd2 running)"
exec sleep infinity
