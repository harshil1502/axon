# AxonOS — Phase 4 boot proof

Captured from the QEMU serial console (aarch64, HVF) booting the Debian
arm64 cloud image provisioned by os/cloud-init. AxonOS reached a login
prompt with the Axon daemon running as a first-class systemd service.

```
==================== AXONOS READY ====================
axon.service : active
kernel       : Linux 6.1.0-52-cloud-arm64
arch         : aarch64
--- action catalogue over the daemon's Unix socket (real Linux) ---
26 actions: app.open, app.quit, clipboard.read, clipboard.write, fs.append, fs.copy, fs.delete, fs.list, fs.mkdir, fs.move, fs.read, fs.search, fs.stat, fs.write, mem.delete, mem.get, mem.put, mem.query, net.fetch, notify, proc.info, proc.kill, proc.list, proc.tree, shell.exec, sys.info
--- a capability-gated call, end to end ---
sys.info -> Linux 6.1.0-52-cloud-arm64 aarch64
======================================================

[    9.473839] cloud-init[451]: AxonOS booted in 9.47 s — the Axon daemon is a first-class Linux service.
Debian GNU/Linux 12 axonos ttyAMA0
```

Kernel: Linux 6.1.0-52-cloud-arm64 (stock, unforked). Boot-to-ready: ~9.5s.
Reproduce: `cd os && ./build-seed.sh && ./boot.sh`
