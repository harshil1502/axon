# Axon build contract (v1)

Every module is built against these **fixed** interfaces. Do not change the
stable core; implement the new interfaces exactly as specified so the pieces
compose. Python ≥3.10, no third-party deps except `anthropic` (optional, in
agent_loop only). Match the existing code's docstring-forward style.

## Stable core (already built — import, do NOT modify)

```python
# axon/registry.py
@dataclass
class Action:
    name: str; description: str; params: dict; handler: Callable
    mutating: bool = False; risk: str = "read"   # risk ∈ {"read","write","dangerous"}
    def schema(self) -> dict            # {name, description, mutating, risk, params}
    def validate(self, args: dict) -> None   # raises ValueError

class Registry:
    def action(self, name, description, params=None, mutating=False, risk="read")  # decorator
    def register(self, action: Action) -> None
    def get(self, name) -> Action        # raises KeyError
    def list(self, pattern="*") -> list[Action]
    def describe(self, pattern="*") -> list[dict]

# param schema shape: {"pname": {"type": "string"|"integer"|"number"|"boolean",
#                                 "required": bool, "desc": str}}

# axon/capabilities.py
class CapabilityError(Exception): ...
@dataclass
class Capability:
    id: str; agent: str; grants: list[str]; path_scopes: list[str]
    issued_at: float; expires_at: float
    def permits_action(self, name) -> bool
    def permits_path(self, path) -> bool
class CapabilityEngine:
    def __init__(self, home: Path)
    def issue(self, agent, grants, path_scopes=None, ttl_seconds=3600) -> str  # token
    def verify(self, token) -> Capability          # raises CapabilityError
    def revoke(self, cap_id) -> None
    def authorize(self, cap, action_name, args) -> None   # raises CapabilityError

# axon/kernel.py
class Kernel:
    def __init__(self, home: Path | None = None)
    home: Path; registry: Registry; caps: CapabilityEngine; audit; journal
    def invoke(self, token: str, action_name: str, args: dict | None = None) -> Any
    def undo(self, audit_id: str) -> str

# axon/audit.py  AuditLog(path): .record(entry: dict) -> str ; .tail(n) -> list[dict]
# axon/__init__.py  boot(home=None) -> Kernel   (returns a kernel with actions registered)
```

## New interfaces to implement (exact signatures)

### axon/policy.py — `PolicyEngine`
```python
class PolicyError(Exception): ...
class PolicyEngine:
    def __init__(self, home: Path) -> None
    # Kernel calls check() right after authorize(); raise PolicyError to block.
    def check(self, cap: Capability, action: Action, args: dict) -> None
    def requires_approval(self, cap: Capability, action: Action, args: dict) -> bool
    # config
    def set_rate_limit(self, pattern: str, max_calls: int, per_seconds: float) -> None
    def deny(self, pattern: str) -> None                 # add action-name glob to denylist
    def pre_approve(self, agent: str, action_name: str) -> None  # one-shot approval, consumed by check()
```
Rules: `risk == "dangerous"` actions require a matching `pre_approve` or `check()`
raises `PolicyError("approval required: ...")`. Per-(agent, action) sliding-window
rate limits. Denylisted patterns always raise. In-process state is fine (rate
windows), but persist denylist/limits config under `home/policy.json`. Use a
monotonic-ish clock via `time.time()`.

### axon/memory.py — `MemoryStore`  + axon/actions/memory.py
```python
class MemoryStore:
    def __init__(self, home: Path) -> None            # persists under home/memory/
    def put(self, key: str, value, *, namespace="default", agent=None) -> None
    def get(self, key: str, *, namespace="default")   # returns value or None
    def query(self, prefix="", *, namespace="default") -> dict   # {key: value}
    def delete(self, key: str, *, namespace="default") -> bool
    def namespaces(self) -> list[str]
# axon/actions/memory.py
def register(reg: Registry, store: MemoryStore) -> None
#   registers mem.put(key,value,namespace?), mem.get(key,namespace?),
#   mem.query(prefix?,namespace?), mem.delete(key,namespace?) — mem.put/delete risk="write"
```
Values are JSON-serializable. Store is durable across process restarts (one JSON
file per namespace, or a single file — your call).

### axon/runtime.py — agent processes + budgets
```python
@dataclass
class Budget:
    tokens: int | None = None; usd: float | None = None
    wall_seconds: float | None = None; actions: int | None = None
@dataclass
class Usage:
    tokens: int = 0; usd: float = 0.0; actions: int = 0; started_at: float = 0.0
class BudgetExceeded(Exception): ...
class AgentProcess:
    pid: str; agent: str; token: str; budget: Budget; usage: Usage
    status: str                      # "running" | "killed" | "done"
    def charge(self, *, tokens=0, usd=0.0, actions=0) -> None   # raises BudgetExceeded
class Runtime:
    def __init__(self, kernel: Kernel) -> None
    def spawn(self, agent, grants, *, path_scopes=None, budget: Budget | None = None,
              ttl=3600) -> AgentProcess           # issues a cap via kernel.caps.issue
    def invoke(self, pid: str, action_name: str, args: dict | None = None) -> Any
    #   charges 1 action to the process, enforces budget, then kernel.invoke(proc.token,...)
    def list(self) -> list[AgentProcess]
    def get(self, pid: str) -> AgentProcess
    def kill(self, pid: str) -> None               # revokes the cap, status="killed"
```

### axon/agent_loop.py — Claude driving Axon through a capability
```python
class AgentLoop:
    def __init__(self, kernel: Kernel, *, model: str = "claude-opus-4-8") -> None
    def run(self, process, task: str, *, max_turns: int = 10) -> str
    #   `process` is any object with .pid, .token, .agent and a .charge(tokens=...) method
    #   (duck-typed AgentProcess — DO NOT import runtime.py; tests pass a fake).
    #   Builds Claude tools from kernel.registry.describe() filtered to the process's
    #   granted actions (kernel.caps.verify(process.token).permits_action). Runs a
    #   MANUAL agentic loop: model="claude-opus-4-8", thinking={"type":"adaptive"}
    #   (NEVER budget_tokens). Executes each tool_use via kernel.invoke(process.token,...),
    #   returns tool_result blocks, charges process.charge(tokens=usage.output_tokens).
    #   Stops on stop_reason=="end_turn" (also handle "refusal","pause_turn"). Returns final text.
    #   GRACEFUL DEGRADE: if `anthropic` import fails OR no credentials, run in a
    #   dry-run mode that returns a clear string like "[dry-run] would run: <task>"
    #   without raising, so tests pass offline.
```
Anthropic usage per the SDK: `client = anthropic.Anthropic()`; `client.messages.create(
model="claude-opus-4-8", max_tokens=8000, thinking={"type":"adaptive"}, tools=tools,
messages=messages)`. Tool input_schema built from action params (map our
{"type","required"} → JSON Schema properties + required list).

### axon/daemon.py + axon/client.py — Axon as a persistent service
```python
# daemon.py — newline-delimited JSON over a Unix domain socket
class Daemon:
    def __init__(self, kernel: Kernel, socket_path: Path | None = None) -> None  # default home/axon.sock
    def handle(self, request: dict) -> dict     # PURE dispatch, testable w/o a socket
    #   request: {"method": "invoke"|"grant"|"actions"|"audit"|"undo"|"revoke", "params": {...}}
    #   response: {"ok": bool, "result": ...} or {"ok": false, "error": "..."}
    def serve_forever(self) -> None             # binds socket, loops
    def stop(self) -> None
# client.py
class Client:
    def __init__(self, socket_path: Path | None = None) -> None
    def call(self, method: str, **params) -> Any    # raises RuntimeError on {"ok":false}
```
`handle()` must be fully testable without binding a socket. Map methods:
invoke→kernel.invoke, grant→kernel.caps.issue, actions→registry.describe,
audit→audit.tail, undo→kernel.undo, revoke→kernel.caps.revoke.

### Action modules — each exposes `register(reg: Registry) -> None`
- **axon/actions/filesystem.py** (REWRITE — keep fs.read/fs.write/fs.list, ADD):
  fs.delete[mutating,risk=write], fs.move[mutating,write], fs.copy[mutating,write],
  fs.mkdir[mutating,write], fs.append[mutating,write], fs.stat[read], fs.search[read]
  (fs.search: find files by glob under a dir). Keep path params named `path`/`src`/`dst`
  so capability path-scoping applies.
- **axon/actions/process.py** (NEW): proc.info(pid)[read], proc.tree[read],
  proc.kill(pid)[risk=dangerous]. Portable via `ps`/`kill`; no psutil.
- **axon/actions/network.py** (NEW): net.fetch(url)[risk=dangerous] using urllib,
  with an allowlist param check — only http/https, return status+truncated body.
- **axon/actions/macos.py** (NEW): app.open(name)[risk=dangerous],
  app.quit(name)[dangerous], notify(text,title?)[write], clipboard.read[read],
  clipboard.write(text)[write] via `osascript`/`open`/`pbcopy`/`pbpaste`.
- **axon/actions/shell.py** (NEW): shell.exec(command)[risk=dangerous] via subprocess,
  returns {stdout,stderr,code}, with a hard timeout.

## Testing rules (critical for parallel builds)
- Put tests in `tests/test_<yourmodule>.py`. Run `cd ~/projects/axon &&
  python3 -m pytest tests/test_<yourmodule>.py -q` and iterate until green.
- Import ONLY the stable core (`axon.kernel`, `axon.registry`, `axon.capabilities`,
  `axon.boot`) and YOUR OWN new module(s). NEVER import another builder's new module
  (they may not exist yet). agent_loop tests use a fake process object.
- Action-module tests: build a fresh `Registry()` (or `Kernel()`), call your
  `register(...)`, then exercise via the registry/kernel. Don't rely on `boot()`
  having your module wired in — that wiring happens later during integration.
- Use `tmp_path` for any home dir. Never touch the real `~/.axon`.
```
