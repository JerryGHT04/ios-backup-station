# Changelog

Notable changes to this project are recorded here. The repository did not previously have a separate changelog; the project README links to this file.

## 2026-11-02

- Completed and verified a direct restore of a 123.6 GB encrypted NAS backup to an iPhone 15 running iOS 27.0; the phone reached the home screen.
- Documented how the iPhone requests backup files over MobileBackup2 and how the phone applies them with BackupAgent2.
- Added estimated total restore duration alongside remaining time, based on observed transfer speed. Estimates are approximate and can change during device-side processing.
- Added an explicit history action to mark a restore successful after confirming the phone reached iOS. Because the phone restarts after receiving the data, transfer completion alone cannot confirm success; the user must wait for iOS, unlock the phone, check the data, and confirm in the history.
- Marked direct NAS restore as experimental in the app and documented the limited device testing and manual success-confirmation step.
- Invited users to report their device model, iOS version, and restore result to help validate more configurations.
- Prevented the device-discovery watchdog from repeatedly restarting stock USB-only usbmuxd when a phone is reachable over Wi-Fi but is not enumerated by USB transport; kept cable-to-Wi-Fi fallback quick and applied cooldowns only after a restart actually succeeds.
