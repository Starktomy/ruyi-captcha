#!/usr/bin/env bash
# Bookworm's HarfBuzz 6 lacks hb_font_set_synthetic_bold (introduced in 7).
# Build the same HarfBuzz release as Ubuntu 24.04, on the older glibc floor.
set -euo pipefail

if pkg-config --atleast-version=7.0.0 harfbuzz harfbuzz-subset; then
  printf 'Using HarfBuzz %s\n' "$(pkg-config --modversion harfbuzz)"
  exit 0
fi

version=8.3.0
checksum=109501eaeb8bde3eadb25fab4164e993fbace29c3d775bcaa1c1e58e2f15f847
work=$(mktemp -d "${RUNNER_TEMP:-/tmp}/ruyijs-harfbuzz.XXXXXX")
trap 'rm -rf "$work"' EXIT
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends meson xz-utils
curl --fail --location --retry 3 \
  "https://github.com/harfbuzz/harfbuzz/releases/download/$version/harfbuzz-$version.tar.xz" \
  --output "$work/harfbuzz.tar.xz"
printf '%s  %s\n' "$checksum" "$work/harfbuzz.tar.xz" | sha256sum --check
tar -xJf "$work/harfbuzz.tar.xz" -C "$work"

meson setup "$work/build" "$work/harfbuzz-$version" \
  --prefix=/usr/local --libdir=lib --buildtype=release --default-library=shared \
  -Dtests=disabled -Ddocs=disabled -Dutilities=disabled -Dintrospection=disabled \
  -Dglib=disabled -Dgobject=disabled -Dcairo=disabled -Dchafa=disabled \
  -Dicu=disabled -Dgraphite2=disabled -Dfreetype=enabled
meson compile -C "$work/build" -j "${JOBS:-$(nproc)}"
sudo meson install -C "$work/build"
sudo ldconfig
# Both the current and pinned older kernel bundlers collect this Debian doc
# path. Supply the notice for the library actually built, including its years.
sudo install -m 644 "$work/harfbuzz-$version/COPYING" /usr/share/doc/libharfbuzz-dev/copyright
test "$(pkg-config --modversion harfbuzz)" = "$version"
test "$(pkg-config --modversion harfbuzz-subset)" = "$version"
printf 'Built HarfBuzz %s on %s\n' "$version" "$(getconf GNU_LIBC_VERSION)"
