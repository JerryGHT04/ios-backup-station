#!/usr/bin/env bash
# ios-backup-station installer. It asks where the backups should be stored, writes .env and starts the container.
# Safe to run again.
#   ./install.sh                                  interactive
#   ./install.sh --path /vol1/ios-backups --yes   no questions
#   ./install.sh --no-start                       only write .env
set -eu
cd "$(dirname "$0")"

ZH=0
case "${LC_ALL:-${LANG:-}}" in zh*) ZH=1 ;; esac
say() { if [ "$ZH" = 1 ]; then printf '%s\n' "$1"; else printf '%s\n' "$2"; fi; }

YES=0; START=1; PATH_ARG=""; PORT_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) YES=1 ;;
    --no-start) START=0 ;;
    --path) PATH_ARG="${2:-}"; shift ;;
    --port) PORT_ARG="${2:-}"; shift ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

# ask "中文提示" "English prompt" "default"  ->  answer in $REPLY
ask() {
  local p; if [ "$ZH" = 1 ]; then p="$1"; else p="$2"; fi
  if [ "$YES" = 1 ]; then REPLY="${3:-}"; return 0; fi
  printf '%s [%s]: ' "$p" "${3:-}"
  IFS= read -r REPLY || REPLY=""
  [ -n "$REPLY" ] || REPLY="${3:-}"
}

env_get() { if [ -f .env ]; then sed -n "s/^$1=//p" .env | tail -n1; fi; }
env_set() {   # env_set KEY VALUE
  [ -f .env ] || cp .env.example .env
  if grep -q "^$1=" .env; then
    awk -v k="$1" -v v="$2" '{ if (index($0, k "=") == 1) print k "=" v; else print }' .env > .env.tmp && mv .env.tmp .env
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}

# 1. Docker
if [ "${IBS_SKIP_DOCKER_CHECK:-0}" != 1 ]; then
  command -v docker >/dev/null 2>&1 || { say "没找到 docker，请先在 NAS 上安装 Docker。" "docker was not found. Install Docker on the NAS first."; exit 1; }
  docker compose version >/dev/null 2>&1 || { say "需要 Docker Compose v2（docker compose 命令）。" "Docker Compose v2 (the 'docker compose' command) is required."; exit 1; }
  docker info >/dev/null 2>&1 || { say "当前账号没有使用 docker 的权限，请换有权限的账号，或在命令前加 sudo。" "This account may not use docker. Use one that can, or prefix the command with sudo."; exit 1; }
fi

# 2. Where should the backups go?
CUR="$(env_get BACKUP_LOCATION)"
if [ -n "$PATH_ARG" ]; then
  LOC="$PATH_ARG"
else
  DEFAULT=""
  if [ -n "$CUR" ]; then
    DEFAULT="$CUR"
    say "已经设置过备份位置：$CUR（直接回车保持不变）" "A backup location is already set: $CUR (press Enter to keep it)"
  else
    say "可以放备份的位置（按剩余空间从大到小）：" "Places that could hold the backups (most free space first):"
    n=0
    while IFS="$(printf '\t')" read -r free mnt; do
      [ -n "$mnt" ] || continue
      base="$mnt"; [ -d "$mnt/$(id -u)" ] && base="$mnt/$(id -u)"
      printf '  %s/ios-backups   (%s GB free)\n' "$base" "$((free / 1048576))"
      [ -n "$DEFAULT" ] || DEFAULT="$base/ios-backups"
      n=$((n + 1))
    done <<EOF
$(df -Pk 2>/dev/null | awk 'NR>1 && $6 ~ /^\/(vol[0-9]*|volume[0-9]*|mnt\/.+|srv|data|share)/ {print $4"\t"$6}' | sort -rn | head -3)
EOF
    [ "$n" -gt 0 ] || say "  （没有自动找到，请自己填一个）" "  (none found automatically, please type one)"
    [ -n "$DEFAULT" ] || DEFAULT="$PWD/backups"
  fi
  ask "备份保存到哪个文件夹？（完整路径）" "Which folder should backups be stored in? (full path)" "$DEFAULT"
  LOC="$REPLY"
fi
case "$LOC" in "~"*) LOC="$HOME${LOC#"~"}" ;; esac
LOC="${LOC%/}"
case "$LOC" in /*) ;; *) say "需要完整路径（以 / 开头）。" "A full path starting with / is needed."; exit 1 ;; esac
[ -n "$LOC" ] || { say "不能用根目录。" "The root folder cannot be used."; exit 1; }

if [ ! -d "$LOC" ]; then
  ask "这个文件夹还不存在，现在创建吗？(y/n)" "That folder does not exist. Create it now? (y/n)" "y"
  case "$REPLY" in
    y|Y|yes|YES)
      mkdir -p "$LOC" 2>/dev/null || { say "创建失败，当前账号没有权限：$LOC。请换一个路径，或用有权限的账号运行。" "Could not create it, this account has no permission: $LOC. Use another path or run as an account that can."; exit 1; } ;;
    *) exit 1 ;;
  esac
fi
T="$LOC/.ibs-write-test.$$"
if ! ( : > "$T" ) 2>/dev/null; then
  say "这个文件夹当前账号不能写入：$LOC" "This account cannot write to the folder: $LOC"; exit 1
fi
rm -f "$T"

FS="$(stat -f -c %T "$LOC" 2>/dev/null || true)"
case "$FS" in
  nfs*|smb*|cifs*|fuse*) say "提醒：这个文件夹在网络共享（$FS）上，备份可能变慢或出错，最好用 NAS 本地磁盘上的文件夹。" "Warning: this folder is on a network share ($FS). Backups may be slow or fail; use a folder on a local disk." ;;
esac
FREE_GB="$(df -Pk "$LOC" 2>/dev/null | awk 'NR==2{printf "%d", $4/1048576}')"
if [ -n "$FREE_GB" ] && [ "$FREE_GB" -lt 50 ]; then
  say "提醒：这里只剩 ${FREE_GB} GB。第一次完整备份可能要 50 GB 以上。" "Warning: only ${FREE_GB} GB free here. A first full backup can need more than 50 GB."
fi

PORT="${PORT_ARG:-$(env_get PORT)}"; PORT="${PORT:-8899}"
if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | awk '{print $4}' | grep -q ":$PORT\$"; then
  say "提醒：端口 $PORT 已经被占用。可以用 --port 换一个。" "Warning: port $PORT is already in use. Pick another one with --port."
fi

env_set BACKUP_LOCATION "$LOC"
env_set PORT "$PORT"
say "已写入 .env：备份位置 = $LOC，网页端口 = $PORT" "Wrote .env: backup location = $LOC, web port = $PORT"

# 3. Things the container needs from the host
[ -S /run/avahi-daemon/socket ] && [ -S /run/dbus/system_bus_socket ] || say "提醒：没找到 avahi（/run/avahi-daemon）。USB 备份可以用，但无线备份找不到手机。" "Warning: avahi (/run/avahi-daemon) was not found. USB backups work, but the phone cannot be found over Wi-Fi."
[ -d /dev/bus/usb ] || say "提醒：没找到 /dev/bus/usb，容器会看不到 USB 设备。" "Warning: /dev/bus/usb was not found; the container will not see USB devices."

if [ "$START" != 1 ]; then
  say "完成。需要时运行：docker compose up -d --build" "Done. Start it when you are ready: docker compose up -d --build"
  exit 0
fi

# 4. Start
if [ -n "$(env_get IBS_IMAGE)" ]; then
  docker compose pull && docker compose up -d
else
  say "第一次需要在 NAS 上从源码构建，大约 5 到 10 分钟，会下载几百 MB，请耐心等待……" "The first start builds from source on the NAS: about 5 to 10 minutes and a few hundred MB. Please wait..."
  docker compose up -d --build
fi

say "等待服务启动……" "Waiting for the service..."
i=0
while [ "$i" -lt 45 ]; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then break; fi
  i=$((i + 1)); sleep 2
done
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"; IP="${IP:-<NAS 的 IP / NAS-IP>}"
echo
say "已启动。接下来：" "Started. Next:"
say "  1. 在同一个局域网的浏览器里打开  http://$IP:$PORT  ，设置访问密码" "  1. Open  http://$IP:$PORT  in a browser on your LAN and set a password"
say "  2. 用 USB 线把 iPhone 或 iPad 接到 NAS，在设备上点“信任”" "  2. Connect the iPhone or iPad to the NAS with a USB cable and tap Trust on it"
say "  3. 页面上选备份位置，然后点“立即备份”（第一次请用 USB 做完）" "  3. Choose the backup location on the page, then Back Up Now (do the first one over USB)"
say "  页面上的“环境自检”会告诉你还有什么没配好。" "  The Setup check on the page tells you if anything is still missing."
