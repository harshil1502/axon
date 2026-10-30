# Axon architecture — an agent-native OS layer

> The thesis, restated: you do not out-macOS macOS. You build the thing it was
> never designed to be — a first-class substrate for AI agents. Human operating
> systems expose a GUI and all-or-nothing permissions; agents are bolted on top,
> scraping pixels and borrowing the user's full authority. Axon inverts that. The
> agent-facing surface *is* the system: a typed API, per-agent capabilities,
> reversibility, audit, memory, and budgeted agent processes, all funnelled
> through a single enforcing chokepoint.

This document describes the whole design: the seven agent-native primitives, the
one path that enforces them, the layer stack from the Linux kernel up, and how
today's macOS daemon becomes the userland of a bootable OS without a rewrite.

It is deliberately honest about what is built, what is specified but not yet
wired, and what is still a plan. See **Current status** at the end for the exact
line.

---

## 1. The seven agent-native primitives

Each primitive answers a question a human OS answers badly (or not at all) for an
agent. Each maps to a concrete module in `axon/`.

### 1. Everything is a typed API — `axon/registry.py`
Every capability the system exposes is a registered `Action`: a namespaced name
(`fs.read`, `proc.kill`), a description, a JSON-schema-ish `params` map, a
`handler`, a `mutating` flag, and a `risk` level (`read` | `write` |
`dangerous`). Agents *discover* the system via `Registry.describe()` and *call*
it — they never impersonate a human at a keyboard. Discovery is the API: an agent
reads the typed catalogue and knows exactly what it can do and how.

### 2. Per-agent capabilities — `axon/capabilities.py`
An agent never gets "root or nothing." It presents a `Capability` token: HMAC-
signed (unforgeable), scoped to an explicit list of action patterns
(`["fs.read", "proc.*"]`), optionally restricted to filesystem path prefixes,
and expiring at a fixed time. Tokens are revocable by id before expiry. Authority
is least-privilege, time-boxed, and attributable by construction.

### 3. Policy & approval gates — `axon/policy.py`
Capabilities say *what an agent may do*; policy says *under what conditions*. The
`PolicyEngine` runs immediately after authorization inside the kernel path. It
enforces per-(agent, action) sliding-window rate limits, an action-name
denylist, and approval gates: any `risk == "dangerous"` action requires a
matching one-shot `pre_approve`, or `check()` raises `PolicyError("approval
required: ...")`. Denylist and limits persist under `home/policy.json`; rate
windows live in-process.

### 4. Reversibility is a primitive — `axon/reversibility.py`
Undo is substrate, not an app feature. Before any mutating action runs, the
`Journal` snapshots prior state under the audit id, so the kernel can roll the
change back (`kernel.undo(audit_id)`). Today this is file-granular copy-on-write
into a snapshot directory; on the bootable OS it upgrades to real CoW snapshots
(btrfs/ZFS) at the same contract. A rogue or mistaken action is always undoable.

### 5. Audit & replay by default — `axon/audit.py`
Every call — authorized, executed, or denied — is appended to a JSONL log
carrying the agent identity, the capability id that authorized it, the params,
and the outcome. In a human OS an agent's actions are indistinguishable from the
user's; here every action is attributable to an agent and the token that let it
happen. Trust through transparency.

### 6. Native memory & context substrate — `axon/memory.py`
Agents need durable, shared, namespaced memory that outlives a single process —
not a bag of files each agent reinvents. `MemoryStore` is a first-class service:
`put` / `get` / `query(prefix)` / `delete` over JSON-serializable values,
partitioned by `namespace`, durable across restarts. It is exposed *through the
same typed API* (`axon/actions/memory.py` registers `mem.put`, `mem.get`,
`mem.query`, `mem.delete`), so memory access is itself capability-gated and
audited.

### 7. Agents as first-class, budgeted processes — `axon/runtime.py`
A human OS schedules processes by CPU and memory. An agent OS must also schedule
by *tokens, dollars, wall-clock, and action count* — the things that actually run
away. The `Runtime` spawns an `AgentProcess`, which owns a capability token and a
`Budget` (`tokens` / `usd` / `wall_seconds` / `actions`). Every `runtime.invoke`
charges the process and raises `BudgetExceeded` before the kernel is ever called;
`kill` revokes the cap and marks the process `killed`. Agents are things the OS
spawns, meters, and reaps — not scripts borrowing your login session.

---

## 2. The chokepoint: one path enforces all seven

The primitives are only trustworthy because there is exactly **one** way to make
anything happen: `Kernel.invoke(token, action_name, args)` in `axon/kernel.py`.
Every agent request flows through it, in a fixed order:

```
kernel.invoke(token, action, args)
    1. verify(token)            capability engine  → authenticate (signature, expiry, revocation)
    2. authorize(cap, ...)      grants + path scopes → is this exact call permitted?
    3. policy.check(cap, ...)   rate limits, denylist, dangerous-approval gate      [integration]
    4. action.validate(args)    registry           → params match the typed schema
    5. journal.snapshot(...)    reversibility       → capture pre-state if mutating
    6. action.handler(**args)   execute the one real side effect
    7. audit.record(...)        log the outcome (also logs step-2/3/4 denials)
```

Because safety, permissions, policy, reversibility, and audit sit *on the only
path*, an agent cannot opt out of them. They are not features layered beside
execution — they are the substrate execution runs on. Steps 1, 2, 4, 5, 7 are
live today in `kernel.py`; step 3 (policy) is written and slots in during
integration (see **Current status**).

### Access surfaces onto the chokepoint
The kernel is reached three ways, all funnelling into the same `invoke`:

- **CLI** — `axon/cli.py`: `axon actions | grant | call | audit | undo | revoke`.
- **Daemon + client** — `axon/daemon.py` / `axon/client.py`: newline-delimited
  JSON over a Unix domain socket. `Daemon.handle(request)` is a *pure* dispatch
  function (`invoke` / `grant` / `actions` / `audit` / `undo` / `revoke`),
  testable without ever binding a socket; `serve_forever()` is the thin
  transport. This is what a long-lived Axon service exposes to everything else on
  the box.
- **Agent loop** — `axon/agent_loop.py`: Claude driving Axon directly. It builds
  Claude tool definitions from `registry.describe()` filtered to the process's
  granted actions, runs a manual agentic loop, executes each `tool_use` through
  `kernel.invoke(process.token, ...)`, and charges the process for output tokens.
  It degrades gracefully to a dry-run string when `anthropic` or credentials are
  absent, so the system is fully functional and testable offline.

---

## 3. The layer stack — Linux kernel up through the agent runtime

Axon does not rewrite an operating system. It adopts the proven one (Linux) as
the execution engine and adds the agent-native authority layer above it. Bottom
to top:

```
┌──────────────────────────────────────────────────────────────────────┐
│  Agents            Claude (agent_loop) · CLI users · other clients     │
├──────────────────────────────────────────────────────────────────────┤
│  Agent Runtime     AgentProcess + Budget: spawn, meter, kill  (prim 7) │   runtime.py
├──────────────────────────────────────────────────────────────────────┤
│  Access surface    Daemon (Unix socket) · Client · CLI                 │   daemon.py / client.py / cli.py
├──────────────────────────────────────────────────────────────────────┤
│  KERNEL CHOKEPOINT verify → authorize → policy → validate → snapshot   │   kernel.py
│                    → execute → audit         (the one enforced path)    │
│    ├─ Capability Engine   signed, scoped, revocable tokens   (prim 2)   │   capabilities.py
│    ├─ Policy Engine       rate limit · denylist · approval   (prim 3)   │   policy.py
│    ├─ Reversibility       snapshot + undo                    (prim 4)   │   reversibility.py
│    ├─ Audit               append-only, attributable          (prim 5)   │   audit.py
│    └─ Memory              durable, namespaced context        (prim 6)   │   memory.py
├──────────────────────────────────────────────────────────────────────┤
│  Action Registry   the typed capability surface             (prim 1)   │   registry.py + actions/*
│    fs.* · proc.* · net.* · shell.* · app.*(macOS) · mem.*              │
├──────────────────────────────────────────────────────────────────────┤
│  Host binding      actions wrap the OS underneath                      │
│    macOS: ps, osascript, pbcopy/pbpaste, open, urllib, subprocess      │
│    Linux: /proc, namespaces, cgroups, netlink, exec — same handlers    │
├──────────────────────────────────────────────────────────────────────┤
│  init / systemd    PID 1 brings the Axon daemon up early               │   packaging/axon.service
├──────────────────────────────────────────────────────────────────────┤
│  Linux kernel      processes, namespaces, cgroups, VFS, syscalls       │   (unmodified — the proven engine)
├──────────────────────────────────────────────────────────────────────┤
│  Hardware / VM     QEMU first, then USB / spare machine                │
└──────────────────────────────────────────────────────────────────────┘
```

**The Action Registry is the seam.** An `Action`'s *signature* is stable across
hosts; only its *handler* binds to the host underneath. `fs.read` wraps portable
Python on macOS and binds to the Linux VFS on the bootable OS — same typed call,
same capability check, same audit line. That is why the same package is both the
macOS daemon and the bootable-OS userland.

---

## 4. How the macOS daemon maps onto the bootable OS

The strategic claim is "same code, two homes." Concretely:

| Concern            | macOS layer (today)                              | Bootable OS (Phase 4)                                   |
|--------------------|--------------------------------------------------|--------------------------------------------------------|
| Where Axon runs    | A userland daemon on a human-first OS, launched manually or via launchd | The userland authority layer, started early by systemd (`axon.service`) |
| Host bindings      | `ps`, `osascript`, `pbcopy`/`pbpaste`, `open`, `urllib`, `subprocess` | `/proc`, cgroups, namespaces, native exec; macOS-only `app.*` actions drop out or gain Linux equivalents |
| Reversibility      | File-copy journal into `home/snapshots`          | Copy-on-write filesystem snapshots (btrfs/ZFS), same `Journal` contract |
| Capability enforcement | In-process at the kernel chokepoint          | Chokepoint **plus** kernel-level containment (seccomp/LSM/namespaces) so bypassing the daemon is not a bypass of the policy |
| Agent processes    | `AgentProcess` metered in-process               | Same, optionally pinned to cgroups/namespaces for real resource isolation |
| Identity of Axon   | One process among many the user owns             | The first-class system authority every agent routes through |

On macOS, Axon is a well-behaved guest: it can only offer what portable userland
tools expose, and an agent that ignores the daemon and shells out directly is
outside Axon's guarantees. That is the honest ceiling of a layer bolted onto a
human OS. The bootable OS removes that ceiling: because Axon owns userland and
Linux enforces isolation beneath it, the capability model can be backed by the
kernel, not merely observed by a cooperating daemon. "PID-adjacent" (see
`docs/BOOT.md`) captures the intent — the Axon daemon sits right above init as
the authority all agent work passes through, even though PID 1 remains systemd
for now.

---

## 5. Current status (honest)

Built and tested (Phase 1–3 core):
- `registry.py`, `capabilities.py`, `kernel.py`, `audit.py`, `reversibility.py`,
  `cli.py` — the chokepoint and its four original primitives are live.
- `policy.py`, `memory.py`, `runtime.py`, `daemon.py`, `client.py`,
  `agent_loop.py` — written against the contract in `docs/CONTRACT.md`.
- Action modules: `actions/filesystem.py`, `actions/system.py` (wired into
  `boot()`); `actions/memory.py`, `actions/process.py`, `actions/network.py`,
  `actions/macos.py`, `actions/shell.py` (written).

Specified and written but **not yet wired** (integration is a separate step, so
this doc does not overclaim):
- `boot()` currently registers only `filesystem` + `system`. Policy, memory,
  runtime, the daemon, and the newer action modules are integrated during the
  integration pass — step 3 (policy) of the chokepoint diagram is therefore
  "written, slotting in," not yet enforced by the shipped `kernel.py`.
- There is no `axon-daemon` console entrypoint yet; the daemon is launched
  explicitly (see `packaging/`), and a proper entrypoint lands in integration.

Still a plan, not code:
- No bootable image exists. The custom Linux distro, QEMU bring-up, and
  USB/spare-machine install are described in `docs/BOOT.md` as the Phase 4 plan.
- Reversibility is file-copy, not CoW; capability enforcement is in-process, not
  kernel-backed; there is no daemon attestation, no multi-user model, no
  syscall-level sandbox yet. These are named openly because the value of an
  agent-native OS is precisely in closing them — and pretending they are done
  would defeat the point.
