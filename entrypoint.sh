#!/bin/bash
# Keep Wi-Fi-capable usbmuxd2 running except during a direct restore, when the
# app switches to the stock USB daemon. The mode files live in /run and are
# intentionally ephemeral across container restarts.
set -u

mkdir -p /var/lib/lockdown /var/run
MODE_FILE=/run/ios-backup-station-usbmuxd.mode
RUNNING_FILE=/run/ios-backup-station-usbmuxd.running

run_usbmuxd() {
  while true; do
    mode=usbmuxd2
    if [ -r "$MODE_FILE" ]; then read -r mode < "$MODE_FILE" || mode=usbmuxd2; fi
    if [ "$mode" = stock ]; then
      printf '%s\n' stock > "$RUNNING_FILE"
      echo "[entrypoint] starting standard USB transport"
      /opt/usbmuxd-stock/sbin/usbmuxd --foreground --user root --config-dir /var/lib/lockdown || true
    else
      printf '%s\n' usbmuxd2 > "$RUNNING_FILE"
      echo "[entrypoint] starting usbmuxd2"
      # usbmuxd2 adds Wi-Fi discovery through the host avahi socket.
      usbmuxd ${USBMUXD_ARGS:--v} || true
    fi
    rm -f "$RUNNING_FILE"
    echo "[entrypoint] USB transport exited, restarting in 1s"
    sleep 1
  done
}

run_usbmuxd &

if [ -f /app/server.py ]; then
  exec python3 -u /app/server.py
fi

echo "[entrypoint] no web app yet, idling (usbmuxd2 running)"
exec sleep infinity
