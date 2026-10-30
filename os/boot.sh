#!/usr/bin/env bash
# Boot AxonOS in QEMU (aarch64) on an Apple-Silicon host, HVF-accelerated.
#
# Downloads a stock Debian arm64 cloud image once, then boots a throwaway
# copy-on-write overlay so every run starts clean. The seed ISO (build-seed.sh)
# provisions Axon on first boot via cloud-init.
#
#   ./build-seed.sh && ./boot.sh
#
# Env: AXON_ACCEL=hvf|tcg (default hvf), AXON_MEM=2048, AXON_TIMEOUT=unset.
# Nothing here touches the host disk — it is a self-contained VM. Never dd this
# to your Mac's drive; the hardware target is a spare machine only (docs/BOOT.md).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BUILD="$HERE/build"; mkdir -p "$BUILD"

QEMU="$(command -v qemu-system-aarch64 || echo /opt/homebrew/bin/qemu-system-aarch64)"
QEMU_IMG="$(command -v qemu-img || echo /opt/homebrew/bin/qemu-img)"
FW="$(ls /opt/homebrew/share/qemu/edk2-aarch64-code.fd 2>/dev/null | head -1 || true)"
[ -n "$FW" ] || { echo "edk2-aarch64-code.fd not found (brew install qemu)"; exit 1; }

IMG_URL="https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-arm64.qcow2"
BASE="$BUILD/debian-arm64-base.qcow2"
DISK="$BUILD/axonos.qcow2"
SEED="$BUILD/seed.iso"
VARS="$BUILD/edk2-vars.fd"
[ -f "$SEED" ] || { echo "no seed image — run ./build-seed.sh first"; exit 1; }

if [ ! -f "$BASE" ]; then
  echo "downloading Debian arm64 cloud image (~350MB, once)…"
  curl -fL --retry 3 -o "$BASE" "$IMG_URL"
fi

# Persistent CoW overlay: created once (cloud-init runs, Node/Claude Code install),
# then reused so the box is ready instantly on later boots. AXON_FRESH=1 resets it.
if [ "${AXON_FRESH:-0}" = 1 ] || [ ! -f "$DISK" ]; then
  rm -f "$DISK"
  "$QEMU_IMG" create -f qcow2 -F qcow2 -b "$BASE" "$DISK" >/dev/null
  echo "created a fresh AxonOS disk — cloud-init provisions on this boot"
else
  echo "reusing AxonOS disk ($DISK) — run 'AXON_FRESH=1 ./boot.sh' to reset"
fi
[ -f "$VARS" ] || dd if=/dev/zero of="$VARS" bs=1m count=64 2>/dev/null

ACCEL="${AXON_ACCEL:-hvf}"
CPU="cortex-a72"; [ "$ACCEL" = hvf ] && CPU="host"
MEM="${AXON_MEM:-2048}"

# Optional watchdog. macOS has no GNU `timeout`; fall back to `gtimeout`
# (brew coreutils) if AXON_TIMEOUT is set.
TIMEOUT_BIN="$(command -v timeout || command -v gtimeout || true)"
WATCHDOG=""
if [ -n "${AXON_TIMEOUT:-}" ]; then
  [ -n "$TIMEOUT_BIN" ] || { echo "AXON_TIMEOUT set but no timeout/gtimeout (brew install coreutils)"; exit 1; }
  WATCHDOG="$TIMEOUT_BIN $AXON_TIMEOUT"
fi

set -x
exec $WATCHDOG "$QEMU" \
  -machine virt -accel "$ACCEL" -cpu "$CPU" -m "$MEM" -smp 2 \
  -drive if=pflash,format=raw,readonly=on,file="$FW" \
  -drive if=pflash,format=raw,file="$VARS" \
  -drive if=virtio,format=qcow2,file="$DISK" \
  -drive if=virtio,format=raw,file="$SEED" \
  -device virtio-net-pci,netdev=n0 -netdev user,id=n0 \
  -nographic -serial mon:stdio
