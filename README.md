# Axon — an agent-native OS

> The thesis: you don't beat macOS/Windows at being macOS/Windows. You beat them
> at the thing they were never designed for — being a first-class substrate for
> AI agents. macOS/Windows were built for humans clicking; agents are bolted on
> top, pixel-scraping screens with all-or-nothing permissions. Axon is designed
> agent-first from the ground up.

Runs today as a daemon on macOS. The same code becomes the userland of a
bootable Linux-based OS (see `docs/BOOT.md`).

## What makes an OS agent-native

1. **Everything is a typed API, not a GUI.** Every capability is a discoverable,
   machine-callable `Action` (26 built in). Agents *call* the system.
2. **Per-agent capabilities.** HMAC-signed, time-boxed, revocable, path-scoped
   tokens grant an explicit set of actions. No root-or-nothing.
3. **Policy engine.** Denylists, per-agent rate limits, and an opt-in approval
   gate for `dangerous` actions — enforced at the kernel's one chokepoint.
4. **Reversibility is a system primitive.** Mutating actions are snapshotted;
   any action can be rolled back.
5. **Native memory substrate.** A durable, namespaced world-model (`mem.*`)
   agents read/write through the same audited path.
6. **Agents are first-class processes.** The runtime spawns agents with
   token/cost/action **budgets** it enforces; a Claude **agent loop** operates
   the machine *through* a capability token — no special powers.
7. **Audit & replay by default.** Every call — allowed or denied — is logged and
   attributable to an agent + the capability that authorized it.

## Architecture

```
Agent Loop (Claude)     drives Axon through a capability token          ✅
Agent Runtime           agents as first-class, budgeted processes       ✅
Memory substrate        durable shared world-model (mem.*)              ✅
Action Registry         every capability = a typed, discoverable call   ✅   (26 actions)
Capability + Policy      per-agent tokens · path scopes · rate/approval  ✅
Reversibility Layer     snapshot + undo any mutation                    ✅
Audit log               attributable, replayable                        ✅
Daemon + Client         Axon as a persistent service (Unix socket)      ✅
Linux kernel            the proven engine — we don't rewrite it       (Phase 4)
```

Every request flows through one chokepoint — `Kernel.invoke()` — which
authenticates the token, authorizes the call, validates params, runs policy,
snapshots for reversibility, executes, and audits. Safety isn't a feature an
agent can bypass; it's the substrate.

## Action library (26)

`fs.*` (read/write/list/append/delete/mkdir/move/copy/stat/search) ·
`mem.*` (put/get/query/delete) · `proc.*` (list/info/tree/kill) ·
`net.fetch` · `shell.exec` · `sys.info` ·
macOS: `app.open/quit`, `notify`, `clipboard.read/write`.
Risk levels: read · write · **dangerous** (net/shell/proc.kill/app.* — gated by policy).

## Quickstart

```bash
python3 -m pip install -e .            # optional Claude loop: pip install -e ".[agent]"

axon actions                                   # discover every capability
TOKEN=$(axon grant claude "fs.*" "mem.*" --scope ~/work --ttl 3600)
axon call "$TOKEN" mem.put key=goal value="ship Axon"
axon call "$TOKEN" fs.write path=~/work/a.txt content="hi"    # allowed
axon call "$TOKEN" fs.write path=~/secrets content=x          # DENIED (out of scope)
axon audit 10                                  # who did what
axon undo <audit_id>                           # roll it back
axon serve                                     # run Axon as a daemon
axon run assistant "summarize my notes" "mem.*" "fs.read"   # let Claude drive Axon
```

State lives under `~/.axon/` (signing key, audit log, snapshots, policy,
memory, revocations).

## The Claude agent loop

`axon run` (or `AgentLoop.run`) spawns a budgeted agent process and lets Claude
(`claude-opus-4-8`, adaptive thinking) drive Axon: the process's granted actions
are exposed as tools, every tool call executes through `kernel.invoke` under the
process's capability, and token usage is charged to the budget. With no
`ANTHROPIC_API_KEY` (or `ant` login) it degrades to a dry-run — set credentials
to run it live.

## Status

Phases 1–3 complete and tested (`pytest` — 77 passing). Phase 4 (packaging as a
bootable Linux distro) is designed in `docs/ARCHITECTURE.md` + `docs/BOOT.md`
with starting artifacts in `packaging/`; that hardware step is deliberate and
separate — never run against a daily-driver Mac.
