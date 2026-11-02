# Changelog

Notable changes to this project are recorded here. The repository did not previously have a separate changelog; the project README links to this file.

## 2026-11-02

### v0.2.0

- Completed and verified a direct restore of an approximately 132.7 GB (123.6 GiB) encrypted NAS backup to an iPhone 15 running iOS 27.0; the phone reached the home screen.
- Documented how the iPhone requests backup files over MobileBackup2 and how the phone applies them with BackupAgent2.
- Added estimated total restore duration alongside remaining time, based on observed transfer speed. Estimates are approximate and can change during device-side processing.
- Added an explicit history action to mark a restore successful after confirming the phone reached iOS. Because the phone restarts after receiving the data, transfer completion alone cannot confirm success; the user must wait for iOS, unlock the phone, check the data, and confirm in the history.
- Marked direct NAS restore as experimental in the app and documented the limited device testing and manual success-confirmation step.
- Kept the restore action in the summary view only, removed the duplicate entry from backup history, and made the experimental label clear in desktop and mobile confirmation dialogs.
- Invited users to report their device model, iOS version, and restore result to help validate more configurations.
- Prevented the device-discovery watchdog from repeatedly restarting stock USB-only usbmuxd when a phone is reachable over Wi-Fi but is not enumerated by USB transport; kept cable-to-Wi-Fi fallback quick and applied cooldowns only after a restart actually succeeds.
- Kept Wi-Fi-capable usbmuxd2 active during normal use and switched to stock USB-only usbmuxd only for the duration of a direct restore, automatically returning to Wi-Fi discovery after completion, failure, or cancellation.
- Changed displayed storage sizes to decimal units (GB = 1,000,000,000 bytes), matching iOS and NAS disk labels.
- Replaced the stale passcode prompt after a quiet period with guidance that the phone may be preparing the backup, and added a two-minute per-device cooldown after a cancelled backup to avoid racing iOS while it closes the session.
- Documented the per-device connection and schedule controls, the serialized single-job limitation, the unverified real-device cancellation snapshot case, and how to use the published GHCR image.
- Moved the repository-scope licensing note out of `LICENSE` so GitHub can identify the application license as MIT; added the note to `THIRD_PARTY_NOTICES.md`.
