# Stage 1: build the libimobiledevice stack and usbmuxd2 from upstream source (recent iOS versions need current upstream).
# Set the APT_MIRROR build argument (for example mirrors.ustc.edu.cn) if downloads from deb.debian.org are slow.
FROM debian:trixie-slim AS build
ENV DEBIAN_FRONTEND=noninteractive
ARG APT_MIRROR=
RUN if [ -n "$APT_MIRROR" ]; then sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; fi
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential clang git ca-certificates autoconf automake libtool pkg-config \
      libusb-1.0-0-dev libavahi-client-dev libssl-dev libreadline-dev libcurl4-openssl-dev \
 && rm -rf /var/lib/apt/lists/*

ENV PKG_CONFIG_PATH=/usr/local/lib/pkgconfig \
    LD_LIBRARY_PATH=/usr/local/lib \
    CPPFLAGS=-I/usr/local/include \
    LDFLAGS=-L/usr/local/lib
WORKDIR /src

# usbmuxd2 does not compile with GCC 14 (inherited ctor with ellipsis), so it is built with clang.
# Order matters: each library depends on the ones before it.
# libgeneral/usbmuxd2 derive their version from the git commit count, so they need a full clone.
RUN set -eux; \
    build() { \
      name="$1"; url="$2"; shift 2; \
      git clone ${FULL:+} $([ "${FULL:-0}" = 1 ] || echo --depth 1) --recursive "$url" "$name"; \
      cd "$name"; \
      ./autogen.sh --prefix=/usr/local "$@"; \
      make -j"$(nproc)"; make install; ldconfig; \
      echo "$name $(git rev-parse --short HEAD)" >> /usr/local/share/build-revisions.txt; \
      cd ..; \
    }; \
    build_full() { FULL=1; build "$@"; FULL=0; }; \
    build_clang() { export CC=clang CXX=clang++; build_full "$@"; unset CC CXX; }; \
    mkdir -p /usr/local/share; \
    build libplist            https://github.com/libimobiledevice/libplist.git --without-cython; \
    build libimobiledevice-glue https://github.com/libimobiledevice/libimobiledevice-glue.git; \
    build libtatsu            https://github.com/libimobiledevice/libtatsu.git; \
    build libusbmuxd          https://github.com/libimobiledevice/libusbmuxd.git; \
    build libimobiledevice    https://github.com/libimobiledevice/libimobiledevice.git --without-cython; \
    build_full libgeneral     https://github.com/tihmstar/libgeneral.git; \
    build_clang usbmuxd2      https://github.com/tihmstar/usbmuxd2.git

# Tiny helper to toggle "sync over Wi-Fi" on the phone (not available in the stock CLI tools).
COPY tools/wifi_sync.c /src/wifi_sync.c
RUN gcc -O2 -o /usr/local/bin/wifi-sync /src/wifi_sync.c $(pkg-config --cflags --libs libimobiledevice-1.0 libplist-2.0)

# Stage 2: slim runtime
FROM debian:trixie-slim
ENV DEBIAN_FRONTEND=noninteractive
ARG APT_MIRROR=
RUN if [ -n "$APT_MIRROR" ]; then sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; fi
RUN apt-get update && apt-get install -y --no-install-recommends \
      libusb-1.0-0 libavahi-client3 libssl3t64 libreadline8t64 libcurl4t64 \
      python3 ca-certificates tzdata procps avahi-utils \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /usr/local /usr/local
RUN echo /usr/local/lib > /etc/ld.so.conf.d/local.conf && ldconfig
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
COPY app /app
ENTRYPOINT ["/entrypoint.sh"]
