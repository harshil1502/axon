# Booting Axon — the plan to make it a real OS

This is the Phase 4 plan: take the Python package that runs as a macOS daemon
today and turn it into the userland of a small bootable Linux system. It is a
plan with runnable first steps, not a finished distro. Every stage below is
ordered by *lowest risk first*, and the whole thing runs in a virtual machine
before it ever touches physical hardware.

## Ground rule: not on your daily driver

Do **not** boot, image, or `dd` any of this onto the Mac you work on, and do not
target its internal disk. Axon's whole point is giving agents real authority over
a machine; you exercise that on a machine you are willing to lose. The target
progression is, in order:

1. **QEMU virtual machine** — a throwaway VM on any host. Nothing outside the VM
   is reachable. This is where 90% of the work happens.
2. **A spare/old physical machine or a USB stick booted on one** — never the
   daily driver, never its primary disk.

If the only hardware available is your working Mac, stop at stage 1 (QEMU). A VM
is a complete, honest environment for everything except "does it POST on bare
metal."

---

## Stage 0 — What we are building

A minimal Linux system whose userland authority layer is the Axon daemon:

- **Kernel:** stock Linux. We do not fork or patch it. It provides processes,
  namespaces, cgroups, and the VFS that Axon's actions bind to.
- **init:** systemd (or a smaller init later). PID 1.
- **Axon daemon:** started early by systemd as `packaging/axon.service`, listening
  on a Unix domain socket. This is the "PID-adjacent" service — not literally PID
  1, but the first-class authority that every agent action routes through,
  ordered right after the base system is up. Making Axon *literally* PID 1 (or
  replacing a login shell with the agent loop) is a later, riskier experiment;
  the honest v1 is "a very early systemd service."
- **A getty / console** for the human operator, plus the `axon` CLI and
  `axon.client.Client` for talking to the daemon over its socket.

Nothing here requires a custom kernel, a custom bootloader, or a rewrite of
libc. That is the point: adopt the proven engine, own the userland.

---

## Stage 1 — Prove it in a container (today, on any host)

Before a rootfs or a bootloader, prove the daemon runs headless with only Python
and the stdlib. `packaging/Dockerfile` does exactly this: install the package,
launch the daemon, keep it in the foreground.

```bash
cd ~/projects/axon
docker build -f packaging/Dockerfile -t axon:dev .
docker run --rm -it axon:dev            # starts the daemon in the foreground
```

This is not "an OS" — a container shares the host kernel — but it validates the
exact process that systemd will supervise later, with zero VM overhead. If the
daemon comes up clean here, the systemd unit is almost entirely about *ordering
and supervision*, not about whether the code runs.

Honest note: `boot()` today wires only the `filesystem` and `system` action
modules, and there is no `axon-daemon` console entrypoint yet, so both the
Dockerfile and the service file launch the daemon with an explicit one-liner
(`python -c "from axon import boot; from axon.daemon import Daemon;
Daemon(boot()).serve_forever()"`). Wiring policy/memory/runtime into `boot()` and
adding a proper `axon-daemon` entrypoint is an integration task; when it lands,
`ExecStart`/`CMD` collapse to `axon-daemon`.

---

## Stage 2 — A minimal rootfs in QEMU

Build the smallest root filesystem that can boot Linux, run systemd, and start
the Axon daemon. Two realistic paths, pick one:

**Path A — Debian `debootstrap` (simplest to reason about).**
```bash
# on a Linux host or Linux VM (debootstrap is Debian/Ubuntu tooling)
sudo debootstrap --variant=minbase --include=systemd,systemd-sysv,python3,ca-certificates \
    stable ./axon-rootfs http://deb.debian.org/debian
sudo cp -r ~/projects/axon ./axon-rootfs/opt/axon
# install the package into the rootfs's python (chroot), create /opt/axon/venv, etc.
sudo cp packaging/axon.service ./axon-rootfs/etc/systemd/system/
sudo chroot ./axon-rootfs systemctl enable axon.service
```

**Path B — Alpine `mkimage` / apk (smaller, musl).** Same shape: a base with
`python3` and an init, the package under `/opt/axon`, the unit enabled. Alpine
uses OpenRC by default; if you go this way, either add systemd or translate
`axon.service` into an OpenRC script. For v1, Debian + systemd is the lower-
surprise choice.

Then turn the rootfs into something QEMU can boot — the low-friction option is to
package it as an initramfs or a raw disk image with a kernel + `init=/lib/systemd/systemd`:

```bash
qemu-system-x86_64 \
  -m 1024 -smp 2 \
  -kernel /path/to/vmlinuz -initrd /path/to/initrd \
  -drive file=axon.img,format=raw,if=virtio \
  -append "root=/dev/vda console=ttyS0 init=/lib/systemd/systemd" \
  -nographic
```

Success criterion for Stage 2: the VM boots to a console, `systemctl status
axon` shows the daemon **active (running)**, and from inside the VM:

```bash
axon actions                                    # the typed catalogue prints
python3 -c "from axon.client import Client; print(Client().call('actions'))"
```

returns the action list over the socket. At that point Axon is a running OS
service, in an OS, in a VM.

---

## Stage 3 — The daemon as a PID-adjacent systemd service

`packaging/axon.service` is the real unit. Key intents:

- `After=network.target`, `WantedBy=multi-user.target` — up as soon as the base
  system is, ahead of interactive login being useful.
- Runs as a dedicated non-root `axon` user whose home is a private state dir
  (`/var/lib/axon`); the kernel derives its state from `Path.home()/.axon`, so
  setting `HOME` steers where capability signing keys, the audit log, and
  snapshots live (there is no `AXON_HOME` hook in the code today — an honest
  detail, and an easy integration follow-up).
- `Restart=on-failure` — the authority layer should come back if it dies.
- Hardening (`ProtectSystem`, `NoNewPrivileges`, `PrivateTmp`) is switched
  **on** but kept conservative; tightening it is deliberate future work, because
  some Axon actions (by design) need broad reach and we do not want to pretend a
  half-configured sandbox is airtight.

"PID-adjacent" is a claim about *position and authority*, not about being PID 1.
The literal-PID-1 / agent-as-init experiments come only after the service model
is boring and reliable.

---

## Stage 4 — Spare machine or USB (only when Stage 2–3 are boring)

Once the VM boots reliably and the daemon behaves, move to hardware you can lose:

1. Write the image to a USB stick with `dd` (triple-check `of=` — this is the one
   irreversible step, and it must be an external stick, **not** your Mac's disk).
2. Boot a **spare/old machine** from it. Expect to iterate on drivers, console,
   and firmware quirks that never showed up under QEMU's virtio devices.
3. Only after it runs on real hardware does it make sense to talk about
   persistence, updates, or a second machine.

There is no step here that targets the daily-driver Mac. If you do not have spare
hardware, Stage 2's QEMU VM is the terminus, and that is a legitimate place to
stop — it demonstrates the full stack.

---

## What "done" means for Phase 4, honestly

- **Done now:** the daemon runs headless (Stage 1 container), and the two
  packaging artifacts exist as realistic starting points.
- **Next, concrete, low-risk:** rootfs + QEMU boot (Stage 2), unit enabled and
  supervised (Stage 3). These are engineering, not research.
- **Deliberately deferred (and named, not hidden):** kernel-backed capability
  enforcement (seccomp/LSM) so bypassing the daemon is not a bypass of policy;
  CoW-snapshot reversibility; a tightened service sandbox; Axon as literal PID 1;
  image signing and update tooling. Until those exist, Axon on Linux is a very
  early systemd service that owns the agent-facing userland — which is exactly
  what this plan claims, and no more.
