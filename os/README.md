# AxonOS — booting Axon as a real OS (Phase 4)

Turns the Axon package into the agent-facing userland of a small bootable Linux
system. The kernel is **stock Linux, unforked** — we adopt the proven engine and
own the userland (see `../docs/BOOT.md` for the full plan and the honest list of
what is deferred).

Everything here runs in a **QEMU virtual machine**. Do not `dd` any of it onto a
machine you care about; the hardware target is a spare box only.

## What it builds

A Debian arm64 cloud image, provisioned on first boot by cloud-init to:

- drop the pure-stdlib Axon source under `/opt/axon` (no apt/pip/venv needed),
- run the Axon daemon as a first-class **systemd service** (`axon.service`,
  "PID-adjacent" — up right after the base system, the authority every agent
  action routes through),
- give the operator an `axon` CLI and identify the box as **AxonOS**,
- and, at boot, exercise the daemon over its Unix socket and print the result to
  the console — so a headless boot proves Axon is actually running on Linux.

## Requirements (macOS / Apple Silicon)

```bash
brew install qemu            # provides qemu-system-aarch64 + edk2 firmware
# hdiutil (seed ISO) is built in. HVF acceleration needs kern.hv_support=1.
```

## Build & boot

```bash
cd os
./build-seed.sh              # bakes the current Axon tree into a cloud-init seed ISO
./boot.sh                    # downloads the base image once, then boots the VM
#   AXON_ACCEL=tcg ./boot.sh # if HVF is unavailable (slower, still works)
```

The console **autologins as root** — first boot streams cloud-init and the
`==== AXONOS READY ====` banner, then drops you straight into a shell
(`root@axonos:~#`; root password is `axon` as a fallback). From inside:

```bash
axon actions                 # the typed capability catalogue, on Linux
systemctl status axon        # the daemon, active (running)
axon audit 10                # who did what, attributable
axon-claude                  # install (first run) & launch Claude Code here
```

`axon-claude` installs Node.js + Claude Code on first run (uses the VM's
network), then launches `claude`. To authenticate a session, either
`export ANTHROPIC_API_KEY=sk-ant-…` (reliable, headless) or use `/login` inside
claude (browser OAuth — the localhost callback is finicky from a VM, so the API
key is the sure path). Axon is reachable from the shell, and from a claude
session, via the `axon` command.

Exit QEMU with `Ctrl-A` then `X`.

## Files

| Path | Role |
|------|------|
| `cloud-init/user-data.tmpl` | first-boot provisioning (systemd unit, CLI, verify) |
| `build-seed.sh` | injects the Axon source, makes the `CIDATA` seed ISO |
| `boot.sh` | downloads the base image, boots QEMU aarch64 + HVF |
| `etc/os-release` | AxonOS identity |
| `openrc/axond` | OpenRC service (Alpine/musl alternative to systemd) |
| `../packaging/axon.service` | the reference systemd unit |
| `../packaging/Dockerfile` | Stage-1 headless container smoke test |

## Status (honest)

- **Works:** a stock Linux kernel boots in a VM and runs the real Axon daemon as
  a supervised service; the capability catalogue is reachable over its socket
  from inside the guest.
- **This is the Stage-2 success criterion from `docs/BOOT.md`**, reached with the
  systemd unit that already ships in `packaging/`.
- **Deliberately deferred (named, not hidden):** kernel-backed capability
  enforcement (seccomp/LSM) so bypassing the daemon isn't a bypass of policy;
  CoW-snapshot reversibility; a tightened service sandbox; Axon as literal PID 1;
  image signing + update tooling; and the bare-metal (Stage 4) step. Until those
  land, AxonOS is a stock-kernel Linux whose agent-facing userland is Axon —
  exactly what this claims, and no more.
