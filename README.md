# ios-backup-station

**English** · [简体中文](README.zh-CN.md)

See [CHANGELOG.md](CHANGELOG.md) for updates.

Back up your iPhone and iPad **to your own NAS, over Wi-Fi or USB**, and manage it from a web page. The backups are full, optionally encrypted device backups in the same folder layout iTunes and Finder use.

<p align="center"><img src="docs/images/hero.png" alt="Desktop and phone interface" width="900"></p>

<p align="center"><a href="docs/video/ios-backup-station-demo.mp4"><img src="docs/images/video-poster.png" alt="Watch the 3-minute demo (no sound, captions in Chinese and English)" width="560"></a><br><sub>▶ Watch the 3-minute demo (no sound, captions in Chinese and English)</sub></p>

> Not affiliated with Apple. Please read the [disclaimer](#5-disclaimer-and-tested-setup) first, **especially the part about what has not been verified yet**.

## Contents

1. [What it is and why](#1-what-it-is-and-why)
2. [Install](#2-install)
3. [Features (screenshots)](#3-features-screenshots)
4. [Permissions it needs](#4-permissions-it-needs)
5. [Disclaimer and tested setup](#5-disclaimer-and-tested-setup)
6. [Known limitations](#6-known-limitations)
7. [Troubleshooting](#7-troubleshooting)
8. [Restoring a backup](#8-restoring-a-backup)
9. [Upgrade, uninstall, back up the settings](#9-upgrade-uninstall-back-up-the-settings)
10. [Security and privacy](#10-security-and-privacy)
11. [How it compares](#11-how-it-compares)
12. [How it works](#12-how-it-works)
13. [Development and tests](#13-development-and-tests)
14. [Credits and license](#14-credits-and-license)
15. [Support](#15-support)

---

## 1. What it is and why

**I did not want to hand my phone backups to iCloud, and I did not want to keep a computer switched on with a cable plugged in.**

- iCloud backups can only go to Apple's servers, and the storage costs extra.
- Wireless sync in iTunes or Finder needs a computer that stays on.
- Most people with a NAS already have lots of space on something that never sleeps.

So this project lets the NAS do the backup itself:

- The NAS talks to the iPhone or iPad over USB or Wi-Fi with [libimobiledevice](https://libimobiledevice.org) and usbmuxd2, the same backup protocol iTunes uses.
- Backups can be **encrypted**. Each device gets a folder named after its UDID, the layout iTunes and Finder use, so you can copy it to a Windows PC or a Mac to restore (see [Restoring a backup](#8-restoring-a-backup)).
- A **web interface** (desktop and phone layouts, works on iPad) sets the backup location and encryption, runs **scheduled backups**, and shows progress and history.
- It is **one Docker container**, started with `docker compose up`.

It is for you if you have a NAS or small server that runs Docker, has a USB port, and sits on the same LAN as your phone.

## 2. Install

### What you need

| Requirement | Notes |
|---|---|
| A Linux NAS or mini server | Tested on x86_64 fnOS, see [tested setup](#5-disclaimer-and-tested-setup) |
| Docker and Docker Compose v2 | `docker compose version` must work |
| A USB port | Pairing and the first full backup are best done over USB |
| avahi on the host (optional) | Only Wi-Fi backups need it; USB backups work without |
| A folder for the backups | With enough space: a full device backup can be tens to hundreds of GB |

### Easiest: the installer

```bash
git clone https://github.com/JerryGHT04/ios-backup-station.git
cd ios-backup-station
./install.sh
```

The script checks Docker, **suggests the location with the most free space**, makes sure the folder exists and is writable, writes `.env`, starts the container and prints the address. The first start builds from source on the NAS and takes a few minutes.

If you run `docker compose up` before there is a `.env`, it stops and tells you what is missing:

```text
required variable BACKUP_LOCATION is missing a value: Set BACKUP_LOCATION in .env first: the full path of the NAS folder where backups are stored ...
```

### Manual install

```bash
cp .env.example .env
# edit .env. Only BACKUP_LOCATION is required: the full path of an existing folder for the backups
docker compose up -d --build
```

Settings in `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `BACKUP_LOCATION` | required | NAS folder for the backups, for example `/volume1/ios-backups` |
| `PORT` | `8899` | Port of the web page |
| `CONFIG_LOCATION` | `./config` | Settings, web password, history. **Keep it.** |
| `LOCKDOWN_LOCATION` | `./lockdown` | Pairing records of your devices. **Keep it**, otherwise you pair again. |
| `APT_MIRROR` | empty | Build with a mirror if downloads are slow, for example `mirrors.ustc.edu.cn` |
| `IBS_IMAGE` | empty | Optional prebuilt image, for example `ghcr.io/jerryght04/ios-backup-station:v0.2.0`; skips the build on the NAS |

### From a NAS Docker app

On fnOS the project shows up in the Docker app's Compose list after you start it with the commands above, where you can inspect and stop it. To edit the compose file in the GUI, stop the project first; it edits the same file.

### First use

1. Open `http://NAS-IP:8899` in a browser on the same LAN and **set a password** (typed twice, hint optional).
2. Connect the iPhone or iPad to the NAS with a USB cable, tap Trust on the device and enter its passcode.
3. The page asks you to **choose the backup folder** (it suggests the disk with the most free space).
4. Do the first backup **over USB, in one go**, and decide about encryption then.
5. For Wi-Fi backups: while on USB, tap Turn On Wi-Fi Sync in Connection & Pairing; on the device go to Settings, Wi-Fi, your home network, ⓘ and **turn off Private Wi-Fi Address** (why: see [Troubleshooting](#7-troubleshooting)); unplug USB and tap Check Connection.
6. For nightly backups, set a schedule.

The **Setup check** on the page tells you what is still missing, for example when the backup folder is not really mounted into the container.

If downloading or building the repository is unreliable in your region, download the source archive from the [v0.2.0 release](https://github.com/JerryGHT04/ios-backup-station/releases/tag/v0.2.0) and set `IBS_IMAGE=ghcr.io/jerryght04/ios-backup-station:v0.2.0` in `.env`. Then run `docker compose pull && docker compose up -d`; this uses the published image instead of building it on the NAS. The image is multi-architecture (amd64 and arm64).

## 3. Features (screenshots)

### Password and language

The first visit sets a password; after that you type it once. Two wrong attempts show the hint you set. On the phone layout a small icon on the password screen switches between English and Simplified Chinese.

<table>
<tr>
<td><img src="docs/images/desktop-password.png" alt="Set a password" width="460"></td>
<td><img src="docs/images/mobile-password-en.png" alt="Set a password, phone" width="190"></td>
<td><img src="docs/images/mobile-language-en.png" alt="Language menu" width="190"></td>
</tr>
</table>

### Summary and backup

The status bar shows the connection and the last backup. Back Up Now starts a backup; the progress bar (and the ring on the phone layout) shows the **overall** progress, not the progress of single files, with elapsed time and an estimate of the time left. Cancelling needs a second tap to confirm and leaves the last good backup untouched.

<table>
<tr>
<td><img src="docs/images/desktop-summary.png" alt="Summary" width="440"></td>
<td><img src="docs/images/desktop-backup-running.png" alt="Backup in progress" width="440"></td>
</tr>
</table>

**Delete backup** removes the whole backup of the selected device from the NAS (you type a confirmation word first; it is refused while a backup runs). **Switch device** appears next to the device name on the desktop layout when more than one iPhone or iPad is visible; it is locked while a task runs.

*The desktop layout is Chinese only for now; the phone layout is available in English and Simplified Chinese. An English desktop layout is on the roadmap.*

### History, schedule, connection

- **Backup history**: time, way (USB or Wi-Fi), trigger (manual or scheduled), size, duration, encryption, and the reason for a failure.
- **Scheduled backup**: the NAS connects to the device at the set time. If the device is offline it retries every 10 minutes inside the window you choose; if a locked device waits for its passcode for more than 5 minutes the attempt is abandoned. It uses the NAS's own time zone.
- **Connection & pairing**: the steps for pairing, turning on Wi-Fi sync and checking the connection, plus an explanation of why Private Wi-Fi Address has to be off.

<table>
<tr>
<td><img src="docs/images/desktop-history.png" alt="History" width="300"></td>
<td><img src="docs/images/desktop-schedule.png" alt="Schedule" width="300"></td>
<td><img src="docs/images/desktop-connection.png" alt="Connection and pairing" width="300"></td>
</tr>
</table>

### Choosing the folder, and the Setup check

The folder picker shows only the **folders you shared with the container** and can create new ones; each device can have its own folder (one for the iPhone, one for the iPad). The Setup check points out when the backup folder is not really mounted, pairing records are not persisted, or USB or avahi cannot be found, and says how to fix it.

<table>
<tr>
<td><img src="docs/images/desktop-folder-picker.png" alt="Choose the backup folder" width="440"></td>
<td><img src="docs/images/desktop-setup-check.png" alt="Setup check" width="440"></td>
</tr>
</table>

### Phone and iPad

Phones and iPads automatically get a layout made for touch, modelled on Apple's Diagnostics app: one thing per screen, a progress ring, cards, details sliding up from the bottom. If you tap Request Desktop Website on a phone you get the desktop layout. The phone layout is in English and Simplified Chinese.

<table>
<tr>
<td><img src="docs/images/mobile-main-en.png" alt="Phone main screen" width="190"></td>
<td><img src="docs/images/mobile-running-en.png" alt="Progress ring" width="190"></td>
<td><img src="docs/images/mobile-backup-en.png" alt="Back Up Now and history" width="190"></td>
<td><img src="docs/images/mobile-connection-en.png" alt="Connection and pairing" width="190"></td>
</tr>
</table>

<p align="center"><img src="docs/images/ipad.png" alt="The interface on an iPad" width="420"></p>

### Try the interface without a phone

```bash
python3 tests/run_demo_server.py     # open http://127.0.0.1:8899, password: demo
```

This is the real server with a fake phone and sample data. It is deleted when you stop it and touches nothing else.

## 4. Permissions it needs

The container has to deal with USB devices, the LAN and the system's avahi, so it needs more than an ordinary app. **Please understand this before installing.**

| Setting | Why | What it means |
|---|---|---|
| `privileged: true` | Access USB devices, make usbmuxd work | A program in the container is effectively root on the NAS. Run it only on a trusted LAN, never expose it to the internet |
| `network_mode: host` | Find the phone on the LAN (mDNS) and reuse the host's avahi | The container uses the host's network; the web port (default 8899) opens on the host |
| `/dev/bus/usb` | USB pairing and backups | The container can see all USB devices |
| `/run/dbus`, `/run/avahi-daemon` | Find phones on Wi-Fi through the host's avahi | The container can talk to the host's avahi service. Without avahi only Wi-Fi backups are affected |
| `/etc/localtime` (read-only) | Scheduled backups use the NAS's time zone | None |
| `BACKUP_LOCATION` (read-write) | Store the backups | The container can read and write this folder only, no other folders |
| `./config`, `./lockdown` (read-write) | Store settings, the password hash and pairing records | `lockdown` holds trust keys of your devices: treat it like a password and do not copy it around |

The container runs as root. At run time it **does not need internet access** (building does: it downloads from GitHub and Debian). It **sends no telemetry**.

## 5. Disclaimer and tested setup

**Disclaimer**

- A hobby project, provided "as is" **without any warranty** (see [LICENSE](LICENSE)).
- Not affiliated with, authorized by or endorsed by Apple. iPhone, iPad, iOS, iTunes, Finder and macOS are trademarks of Apple Inc., used here only to say which devices and programs the software works with.
- A backup holds your most private data (photos, messages, health data, passwords). **Turn encryption on, keep the backup password safe, and try a restore from time to time.** A forgotten backup password cannot be recovered.
- It relies on a backup protocol implemented by third-party libraries. After an iOS update the protocol may change, and backups can fail or be incomplete.
- The author is not responsible for data loss.

**What has been tested (the author's setup)**

| Item | Details |
|---|---|
| NAS | fnOS (kernel 6.18), x86_64, 12 cores, 15.5 GiB RAM, btrfs storage |
| Docker | Docker 28.5.2, Compose v2.40.3, Debian 13 (trixie) base image |
| Device | iPhone Air (`iPhone18,4`), iOS 27.0.1, USB-C; an iPad was used to open the web interface |
| Network | Router with 2.4 GHz and 5 GHz, NAS on Ethernet |
| Browsers | Safari on an iPhone and an iPad; Chrome on Linux for the automated tests |
| Resource use | About 1% of one CPU core and about 19 MiB of RAM while idle |

**Verified on real hardware**: USB detection and pairing; turning on Wi-Fi sync; finding the phone over Wi-Fi and switching between USB and Wi-Fi; turning on backup encryption; opening the web interface on the phone and the iPad; starting and cancelling a backup; **complete encrypted backups over Wi-Fi and USB** (an iPhone, about 140.6 GB, and an iPad, about 35.4 GB); deleting a backup; and **a complete direct restore from an approximately 132.7 GB (123.6 GiB) encrypted NAS backup to the same iPhone 15 running iOS 27.0, reaching the home screen**.

**Not verified yet (do not assume they work)**

- Unattended nightly backups (in particular whether a locked phone asks for its passcode).
- Restoring an iPad. (Backing an iPad up works; direct restore has only been completed on an iPhone 15 so far.)
- The automatic choice of layout when you tap Request Desktop Website, and the iPad getting the phone layout: the decision logic was tested in simulation, the behaviour on real devices has not been checked item by item.
- Other NAS brands (Synology, QNAP, Unraid, TrueNAS, ...), arm64, other iOS versions.

**Automated tests** (no device needed: a fake set of libimobiledevice commands drives the real server code): 39 API tests, 13 browser tests (sign-in, every screen, cancelling, XSS, no sideways scrolling at any width, every control has a name) and 35 pure-logic tests (schedules, watchdogs, progress parsing, cooldowns and self-check). See [Development and tests](#13-development-and-tests). They catch mistakes in our own logic but **are no substitute for real hardware**.

## 6. Known limitations

- **No resume.** If a backup is interrupted (the device leaves Wi-Fi, the cable is pulled, you leave home), the files already written stay, but next time the device compares again and sends what is needed; the interrupted part has to be sent again. Do the first full backup at home over USB in one go.
- **One backup or restore runs at a time.** The device list, connection actions and schedules are per device, but backup jobs are serialized to avoid competing MobileBackup2 sessions. On desktop and phone layouts, use the device picker to switch the selected device. Backup folder and schedule are stored per device.
- **A cancelled incremental backup's previous snapshot has not been verified on real hardware.** The backup tool uses snapshot folders, but this exact interruption case has not been tested with a real phone; inspect the backup before relying on it as the only copy.
- **A backup can be larger than the used-storage figure shown by iOS.** Filesystem clones and compression do not carry over into the backup, so leave extra NAS space.
- **Sizes use decimal units** (1 GB = 1,000,000,000 bytes), matching the storage units shown by iOS and NAS disks.
- **A locked device may ask for its passcode.** Right after a restart, before the first unlock, a backup always asks; otherwise it depends on the device and has not been tried overnight yet.
- **No browsing of single files.** It is a full device backup that can only be restored as a whole.
- Direct restore is available from the NAS web page over USB. Apple Devices/iTunes on Windows or Finder on a Mac remain alternatives.
- **The desktop layout is Chinese only for now.** The phone layout has English and Simplified Chinese.
- **The web page is plain HTTP, no TLS.** Only for the LAN, see [Security and privacy](#10-security-and-privacy).
- **Wi-Fi backups are slower than USB** and use Wi-Fi bandwidth. Do them at night.
- Since iOS 26.4 there is a device-side LAN connection problem that needs Private Wi-Fi Address turned off.

## 7. Troubleshooting

**The Setup check says the backup folder is not mounted**
The container cannot see your folder, so backups would be written inside the container and lost when it is removed. Check that `BACKUP_LOCATION` in `.env` is an existing path and run `docker compose up -d` to recreate the container.

**The NAS cannot find the phone over Wi-Fi**
1. Are they on the same network segment? Guest networks and some IoT networks isolate devices.
2. Is Wi-Fi sync on? (Turn it on while connected over USB.)
3. Turn off **Private Wi-Fi Address** for your home network (Settings, Wi-Fi, ⓘ next to the network). Since iOS 26.4, leaving it on makes the device unreliable on the LAN: the NAS sees it one moment and loses it the next. If your 2.4 GHz and 5 GHz networks have different names, do both.
4. A sleeping device may not answer. Charge it, or wake the screen and tap Check Connection.
5. Is avahi running on the host? The Setup check says so.

**The connection shown is wrong after switching Wi-Fi band or the USB cable**
usbmuxd2 sometimes does not rediscover a device, so the service has two watchdogs: a device on the USB bus that usbmuxd2 does not list gets usbmuxd2 restarted after 3 seconds; a phone that is reachable on the LAN but not listed after about 8 seconds (1 second right after a cable was pulled). Restarting usbmuxd2 takes about twenty seconds on real hardware, so the whole thing can take half a minute. Check Connection triggers it on demand.

**A backup starts and waits for the device passcode**
Unlock the device and enter its passcode. If the status stays quiet after 15 seconds, the page says that the phone may be preparing the backup; it also reminds you to unlock it if you have not entered the passcode. A scheduled backup gives up after 5 minutes of waiting and retries later.

**A new operation fails right after cancelling a backup**
The device may need time to finish closing its previous backup session. The app blocks new backup, restore, pairing, Wi-Fi sync and encryption operations for that device for up to two minutes and shows the remaining wait. Keep the device unlocked; if it still fails after the wait, reconnect USB.

**I forgot the web password**
Delete `config/auth.json` in the project folder on the NAS, restart the container and set a new one. This does **not** affect the backups or the backup password on the device.

**I forgot the backup password**
It cannot be recovered; that is how iOS works. Keep the backup password in a password manager.

**The build is slow or fails**
If deb.debian.org is slow, set `APT_MIRROR` in `.env` (for example `mirrors.ustc.edu.cn`). Docker registry mirrors sometimes answer 401; trying again usually works.

**The time is wrong**
Scheduled backups use the NAS's time zone; the compose file mounts `/etc/localtime`. Run `date` on the NAS to check.

## 8. Restoring a backup

**Experimental: Restore directly from NAS.** We tested an approximately 132.7 GB (123.6 GiB) encrypted backup on an iPhone 15 running iOS 27.0 and confirmed the phone reached the home screen. More testing is needed across other devices, iOS versions, and backup conditions. We welcome community testing; please report the device model, iOS version, and whether the restore completed successfully. A backup can only be restored to the device it came from, and the target iOS version must be at least as new as the backup. Keep the USB connection during restore. After receiving the data, the phone restarts; the NAS cannot confirm completion while that happens. Wait for the phone to reach iOS, unlock it, and check the data before clicking **Confirm Restore Success** in the history.

How restore works: the restore worker runs on the NAS, while Apple's BackupAgent2 on the iPhone decrypts and applies the data. The phone requests manifests and files in batches over Apple's MobileBackup2 protocol; the NAS reads the requested content from the original backup and sends it to the phone over USB/usbmuxd. Before starting, the NAS verifies the backup password and reads the encrypted manifest; it does not first decrypt the entire photo and app-data payload onto the NAS. The phone processes the received data, completes setup, and restarts. While the phone restarts, the NAS cannot tell whether iOS finished applying the restore, so **“Transfer complete” means only that protocol transfer ended; it does not mean the restore succeeded**. Wait until the phone reaches the lock or home screen, unlock it, and check the data. Then click **Confirm Restore Success** in the history; only then does the entry turn green as **Restore successful**.

Direct restore runs an isolated MobileBackup2 protocol worker and keeps per-attempt diagnostics in the configuration directory under `restore-logs/`. The page shows sent bytes and estimated total and remaining time based on measured transfer speed; these are approximate and may change while the phone processes data or restarts. See [Restore engine](docs/restore-engine.md).

During normal use, `usbmuxd2` stays active so the NAS can discover devices over Wi-Fi. Only while a direct restore is running, the service temporarily switches to the standard USB-only `usbmuxd`; after the restore ends, including cancellation or failure, it automatically switches back to `usbmuxd2` so Wi-Fi discovery resumes.

If direct restore does not work, use Apple Devices, iTunes, or Finder as described below:

**Windows (the Apple Devices app, or iTunes from the Microsoft Store)**

1. Copy the backup folder from the NAS (its name is the device's UDID) to the Windows PC.
2. Put it in `%USERPROFILE%\Apple\MobileSync\Backup\` (for example `C:\Users\<you>\Apple\MobileSync\Backup\`) and **do not rename it**. The older iTunes from Apple's website uses `%APPDATA%\Apple Computer\MobileSync\Backup\` instead.
3. Turn off Find My iPhone on the device and connect it. In iTunes click the phone icon at the top left and click Restore Backup in Summary; in the Apple Devices app select the device and click Restore Backup... under General. Choose the backup and enter the backup password if it is encrypted.

**macOS (Finder)**

1. Copy the backup folder to the Mac (in Finder use Go, Connect to Server to reach the NAS).
2. In Finder press ⇧⌘G, paste `~/Library/Application Support/MobileSync/Backup/`, drag the folder in and do not rename it.
3. Connect the iPhone, select it in the Finder sidebar, click Restore Backup... under General and choose the backup.
4. macOS Mojave and older use iTunes, same steps as Windows.

The History page has a copy button for these paths. **Try a restore on a spare device before you really need it.**

## 9. Upgrade, uninstall, back up the settings

**Upgrade**

```bash
git pull
docker compose up -d --build     # or, with IBS_IMAGE set: docker compose pull && docker compose up -d
```

The `config` and `lockdown` folders are untouched, so password and pairing stay.

**Uninstall**

```bash
docker compose down              # removes the container; backups, config and lockdown stay
docker rmi ios-backup-station    # removes the image (optional)
```

To remove everything, also delete the backup folder, `config` and `lockdown` by hand.

**Back up this project's own settings**
Copy the `config` and `lockdown` folders: they hold the settings, the password hash, the history and the pairing records. **`lockdown` contains trust keys, keep it like a password.**

## 10. Security and privacy

- **All data stays on your LAN and your NAS.** The program does not phone home.
- **Web password**: stored as a salted PBKDF2-SHA256 hash (200,000 iterations), never in clear text; the session is a signed cookie (HttpOnly, SameSite=Lax) valid for 30 days; ten wrong attempts from one address lock it for a minute; the hint appears only after two wrong attempts.
- **The backup password** is not stored on the NAS: it passes through the service once, when you turn encryption on or off (handed to the backup tool through an environment variable), and then lives on the device. It does not appear in the logs (a test checks this).
- **The page is plain HTTP**, so other devices on the LAN could in theory read the password. Do not forward the port to the internet. If you need that, put it behind a reverse proxy with HTTPS.
- Text that comes from a device or a folder is shown as plain text and never run as HTML (a test checks this).
- The container is privileged (see [Permissions](#4-permissions-it-needs)).

Please report security problems through an issue; for a serious vulnerability, contact the maintainer privately first.

## 11. How it compares

This comparison summarizes each project's current public documentation; **we have not installed and tested the other projects feature by feature**. Features and licensing can change, so check the linked project pages. Wi-Fi sync and backup are different: Apple's Wi-Fi sync documentation describes syncing content between a computer and a device, not a NAS-scheduled backup.

| | ios-backup-station | iTunes / Finder / Apple Devices | [AirVault](https://github.com/wizier/airvault) | [iosbackup](https://github.com/razeencheng/iosbackup) |
|---|---|---|---|---|
| Backup storage | A NAS folder you choose; whole-device backup in an iTunes-compatible layout | Local computer | Local NAS directory; an Unraid app template is available | A chosen directory on Linux or NAS |
| Computer needed day to day | No; the NAS runs the service | Managed by a computer app; the computer must be on for Wi-Fi sync | No | No |
| Connection | USB or Wi-Fi backup; direct restore from NAS currently uses USB | USB; Wi-Fi sync must first be enabled from the computer | USB or Wi-Fi | USB or Wi-Fi |
| Backup trigger | Start from the web UI or schedule the NAS to connect and retry within a time window | Start backup from the computer app; Wi-Fi sync of content can start automatically when the device is charging and the computer is on the same Wi-Fi | Automatic backup windows; see project docs | Manual or per-device schedules with time windows, battery, and charging conditions |
| Backup history | Shows task records; does not offer selectable historical restore points. Use NAS snapshots if you need retained versions | Managed by the computer's device-backup interface | Incremental, versioned backups with selectable restore points | Public docs describe status and file listing; check project docs for retention behavior |
| Encryption | Supports encrypted iOS backups; the backup password is not stored on the NAS | Supports encrypted local backups | Optional backup encryption | Supports backup encryption and password management |
| Restore | Direct restore from NAS to the original device is **experimental**; alternatively copy the backup to a computer and restore with Apple software | Restore from a backup stored on the computer | Web UI supports restore and choosing a restore point | Whole-device restore is marked experimental and disabled by default; Apple Devices, iTunes, or Finder can also be used |
| Web and management | Desktop, phone, and iPad web UI; setup checks, progress, and task history | No NAS web-management UI | Web UI; Unraid app template | Web UI; backup status, stages, file listing, and optional notifications |
| Open-source license | MIT | Proprietary Apple software | GPL-3.0-or-later | AGPL-3.0-only |
| Platform and validation | Docker; direct NAS restore tested on iPhone 15 with iOS 27.0 | Apple Devices / iTunes on Windows, or Finder on macOS | Docker, with an Unraid template; project lists amd64 and arm64 | Linux / NAS Docker; project lists amd64 and arm64 |

### Choosing between them

- **For NAS-managed backups and a browser interface:** both ios-backup-station and iosbackup support USB/Wi-Fi and NAS-scheduled jobs. ios-backup-station's scheduler connects from the NAS at the configured time; iosbackup also documents battery, charging, and time-window conditions plus optional notifications.
- **For retained versions and choosing a restore point:** AirVault documents incremental, versioned backups and selectable restore points. ios-backup-station records task results but does not currently expose historical backup snapshots; NAS filesystem snapshots can be configured separately.
- **For Apple's familiar workflow or computer-local backups:** use Apple Devices, iTunes, or Finder. Backups live on the computer; Wi-Fi sync requires the computer to be on and on the same Wi-Fi. Apple's guide says to enable Wi-Fi sync from the computer first; this does not provide the same NAS-initiated schedule as the NAS tools above.
- **For restoring directly from a NAS web UI:** ios-backup-station has USB direct restore, labeled experimental. So far we have completed a restore of an approximately 123.6 GiB encrypted backup on iPhone 15 (iOS 27.0), reaching the home screen; after the phone restarts, the result still needs manual checking and confirmation. iosbackup also lists whole-device restore as experimental and disabled by default; AirVault documents web restore and restore-point selection.
- **For an Unraid-specific installer:** AirVault offers an Unraid Community Apps template. Other NAS/Linux hosts can also use Docker, following the relevant project instructions.

Our tradeoff is an MIT-licensed NAS web app with an iTunes-compatible backup, scheduled tasks, setup checks, and a newly tested direct-restore path. It does not yet provide AirVault's documented versioned restore points, and direct restore still needs community testing across more devices and iOS versions. This summary is based on the [AirVault project page](https://github.com/wizier/airvault), [iosbackup documentation](https://github.com/razeencheng/iosbackup), and [Apple's Wi-Fi sync guide](https://support.apple.com/en-sg/guide/itunes/itns3751d862/windows); it is not a same-conditions performance or reliability benchmark.

## 12. How it works

```mermaid
flowchart LR
  B[Browser / phone / iPad] -- HTTP 8899 --> S[server.py: web page and API]
  S -- runs --> T[libimobiledevice tools<br/>idevicebackup2 and others]
  T -- USB or Wi-Fi --> D[(iPhone / iPad)]
  U[usbmuxd2] <-- connection management --> T
  U -- USB --> D
  A[host avahi / mDNS] -- discovery --> U
  S -- watchdogs --> U
  T -- writes --> F[(backup folder)]
```

- `app/server.py`: the web service, written with the Python standard library only, no third-party dependencies. It **runs the command line tools**, parses the overall progress from their output and manages jobs, the schedule and the watchdogs.
- `app/static/index.html` and `mobile.html`: the two interfaces, one file each, no build step.
- `tools/wifi_sync.c`: a very small tool that turns on the device's "Wi-Fi sync" (the stock tools cannot).
- `Dockerfile`: builds the libimobiledevice libraries and usbmuxd2 from upstream source (see [Credits](#14-credits-and-license)).

## 13. Development and tests

No device is needed. `tests/fakephone/` is a fake set of libimobiledevice commands controlled by one state file (unplug USB, drop Wi-Fi, lock the phone, fail a backup, ...), and the real server code runs on top of it.

```bash
python3 -I tests/test_logic.py                # pure logic, under a second
python3 -I tests/test_backend.py              # API level, about 4 minutes (one test waits for the scheduler to fire)
pip install playwright pillow                 # the browser tests use the Chrome you already have
python3 -I tests/test_ui.py                   # browser level, about 2 minutes
python3 tests/run_demo_server.py              # the demo with sample data
python3 tests/make_screenshots.py             # regenerate docs/images
```

Real problems these tests have already caught: a mistyped log line that left the job state uncleared when a backup ended, a missing argument that made every backup fail, GET requests with a null byte that crashed the handler thread, dialogs that could not be closed with Esc, and a progress bar that showed the progress of single files as the overall progress.

## 14. Credits and license

The code of this project is under the [MIT license](LICENSE).

It depends on the projects below. The image contains them, each under its own license (details in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)):

- [libimobiledevice](https://github.com/libimobiledevice/libimobiledevice) (including `idevicebackup2`), libplist, libusbmuxd, libimobiledevice-glue, libtatsu: LGPL-2.1
- [usbmuxd2](https://github.com/tihmstar/usbmuxd2) and libgeneral (tihmstar): LGPL-3.0 and LGPL-2.1
- The Debian base image and packages

Thanks to the authors of these projects; this one would not exist without them.

## 15. Support

This project is free and MIT licensed, and everything works without paying. If it saved you some trouble and you would like to buy me a coffee, you can scan the WeChat code below. It is entirely optional and gives no extra features or support.

<p align="center"><img src="docs/images/wechat-pay.png" alt="WeChat payment code" width="220"></p>
