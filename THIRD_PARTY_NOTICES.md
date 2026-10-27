# Third-party notices

The code in this repository is MIT licensed (see `LICENSE`). The Docker image that the `Dockerfile` builds also
contains other people's software. It is **not** part of this repository: the `Dockerfile` downloads it from the
upstream projects while building, and the image uses the libraries as shared libraries (`/usr/local/lib`), so they
can be replaced by rebuilding with a different upstream version.

| Project | License | Source |
|---|---|---|
| libimobiledevice (includes the `idevicebackup2`, `idevice_id`, `ideviceinfo`, `idevicepair` tools) | LGPL-2.1-or-later | https://github.com/libimobiledevice/libimobiledevice |
| libplist | LGPL-2.1 | https://github.com/libimobiledevice/libplist |
| libusbmuxd | LGPL-2.1 | https://github.com/libimobiledevice/libusbmuxd |
| libimobiledevice-glue | LGPL-2.1 | https://github.com/libimobiledevice/libimobiledevice-glue |
| libtatsu | LGPL-2.1 | https://github.com/libimobiledevice/libtatsu |
| libgeneral | LGPL-2.1 | https://github.com/tihmstar/libgeneral |
| usbmuxd2 | LGPL-3.0 | https://github.com/tihmstar/usbmuxd2 |
| Debian GNU/Linux base image and packages (python3, libusb, avahi client libraries, OpenSSL, coreutils and others) | various, see `/usr/share/doc/*/copyright` inside the image | https://www.debian.org |

The licenses above were read from the license files in the upstream repositories when this project was prepared
(October 2026). Upstream projects can change their licenses, so check them yourself before redistributing an image.
This list is informational and is not legal advice.

The `Dockerfile` builds the current default branch of each project. The revisions used for the first working build were:

```
libplist              fe3dc34
libimobiledevice-glue 10ddafa
libtatsu              e7d6ad1
libusbmuxd            93eb168
libimobiledevice      fa0f791
libgeneral            81e4636
usbmuxd2              744c46f
```

A built image records the revisions it was made from in `/usr/local/share/build-revisions.txt`.

## Trademarks

iPhone, iPad, iOS, iPadOS, iTunes, Finder and macOS are trademarks of Apple Inc. This project is independent and is
not affiliated with, authorized by or endorsed by Apple. The names are used only to describe which devices and
programs the software works with. The device icons in the web interface are original drawings, and the "iTunes-like"
look of the desktop page is an imitation of general layout ideas, not Apple artwork.
