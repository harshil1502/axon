#!/usr/bin/env bash
# Build the cloud-init NoCloud seed ISO that provisions AxonOS on first boot.
# Injects the Axon source tarball (base64) into user-data, then makes a CIDATA
# ISO with hdiutil (macOS-native — no mkisofs/xorriso needed).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BUILD="$HERE/build"
SEED_DIR="$BUILD/seed"
PAYLOAD="$HERE/payload/axon-src.tar.gz"
mkdir -p "$SEED_DIR"

# (Re)build the source payload so the seed always reflects the current tree.
( cd "$HERE/.." && tar czf "$PAYLOAD" axon pyproject.toml README.md docs )

# Optional: bake an Anthropic API key into the seed so the VM auto-authenticates
# Claude Code. Sourced from the host env or a git-ignored file — never committed,
# and (when built locally) never routed through anything but this machine.
KEY="${ANTHROPIC_API_KEY:-}"
if [ -z "$KEY" ] && [ -f "$HERE/secrets/anthropic.key" ]; then
  KEY="$(tr -d '\r\n' < "$HERE/secrets/anthropic.key")"
fi
export AXON_INJECT_KEY="$KEY"

# Render user-data: substitute the base64 payload (python avoids sed's line-length
# limits on the ~120KB blob) and the ANTHROPIC_API_KEY env line.
python3 - "$HERE/cloud-init/user-data.tmpl" "$PAYLOAD" "$SEED_DIR/user-data" <<'PY'
import base64, os, sys
tmpl, payload, out = sys.argv[1:4]
b64 = base64.b64encode(open(payload, "rb").read()).decode()
key = os.environ.get("AXON_INJECT_KEY", "").strip()
if key:
    anth = "export ANTHROPIC_API_KEY='%s'" % key.replace("'", "'\\''")
else:
    anth = ("# no ANTHROPIC_API_KEY baked in — set it in the VM (export ...), or\n"
            "# re-run build-seed.sh with ANTHROPIC_API_KEY exported / os/secrets/anthropic.key")
txt = open(tmpl).read().replace("__AXON_B64__", b64).replace("__ANTHROPIC_ENV__", anth)
open(out, "w").write(txt)
PY
cp "$HERE/cloud-init/meta-data" "$SEED_DIR/meta-data"
if [ -n "$KEY" ]; then
  echo "🔑 baked ANTHROPIC_API_KEY into the seed — the VM will auto-authenticate Claude Code"
else
  echo "ℹ️  no ANTHROPIC_API_KEY baked in — set it inside the VM, or export it before build-seed.sh"
fi

# CIDATA-labelled ISO9660 image = the NoCloud datasource cloud-init looks for.
ISO="$BUILD/seed.iso"
rm -f "$ISO"
hdiutil makehybrid -quiet -o "$ISO" -iso -joliet -default-volume-name CIDATA "$SEED_DIR"
echo "seed image: $ISO"
ls -la "$ISO"
