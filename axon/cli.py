"""axon CLI — drive the kernel from the terminal.

    axon actions                          # discover every capability
    axon grant <agent> <grants...>        # mint a capability token
    axon call <token> <action> [k=v...]   # invoke an action as that agent
    axon audit [n]                         # tail the audit log
    axon undo <audit_id>                   # roll back a mutating action
    axon revoke <cap_id>                   # kill a token early
    axon serve                            # run Axon as a daemon (Unix socket)
    axon run <agent> <task> [grant...]    # let Claude drive Axon under a budget

Grants and path scopes make the agent-native model tangible: an agent can only
do what its token says, within the paths its token allows, until it expires.
"""

from __future__ import annotations

import json
import sys

from . import boot
from .capabilities import CapabilityError


def _parse_kv(pairs: list[str]) -> dict[str, str]:
    args: dict[str, str] = {}
    for p in pairs:
        if "=" not in p:
            sys.exit(f"bad arg (expected key=value): {p}")
        k, v = p.split("=", 1)
        args[k] = v
    return args


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 0

    kernel = boot()
    cmd, rest = argv[0], argv[1:]

    if cmd == "actions":
        pattern = rest[0] if rest else "*"
        for a in kernel.registry.describe(pattern):
            flag = " [mutating]" if a["mutating"] else ""
            print(f"{a['name']:<12} {a['description']}{flag}")
        return 0

    if cmd == "grant":
        if len(rest) < 2:
            sys.exit(
                "usage: axon grant <agent> <grant> [grant...] [--scope PATH] [--ttl N]"
            )
        agent = rest[0]
        scopes: list[str] = []
        ttl = 3600
        grants: list[str] = []
        it = iter(rest[1:])
        for tok in it:
            if tok == "--scope":
                scopes.append(next(it))
            elif tok == "--ttl":
                ttl = int(next(it))
            else:
                grants.append(tok)
        token = kernel.caps.issue(agent, grants, scopes, ttl)
        print(token)
        return 0

    if cmd == "call":
        if len(rest) < 2:
            sys.exit("usage: axon call <token> <action> [key=value...]")
        token, action = rest[0], rest[1]
        args = _parse_kv(rest[2:])
        # Coerce a couple of well-known numeric params.
        if "limit" in args:
            args["limit"] = int(args["limit"])  # type: ignore[assignment]
        try:
            result = kernel.invoke(token, action, args)
        except (CapabilityError, KeyError, ValueError) as exc:
            print(f"DENIED: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2) if not isinstance(result, str) else result)
        return 0

    if cmd == "audit":
        n = int(rest[0]) if rest else 20
        for e in kernel.audit.tail(n):
            status = e.get("status", "?")
            print(
                f"{e['id']}  {status:<10} {e.get('agent', '?'):<8} "
                f"{e.get('action', '?'):<10} {e.get('error', e.get('undo_id', ''))}"
            )
        return 0

    if cmd == "undo":
        if not rest:
            sys.exit("usage: axon undo <audit_id>")
        print(kernel.undo(rest[0]))
        return 0

    if cmd == "revoke":
        if not rest:
            sys.exit("usage: axon revoke <cap_id>")
        kernel.caps.revoke(rest[0])
        print(f"revoked {rest[0]}")
        return 0

    if cmd == "serve":
        from .daemon import Daemon

        d = Daemon(kernel)
        sock = getattr(d, "socket_path", kernel.home / "axon.sock")
        print(f"axon daemon listening on {sock}  (Ctrl-C to stop)")
        try:
            d.serve_forever()
        except KeyboardInterrupt:
            d.stop()
            print("\nstopped")
        return 0

    if cmd == "run":
        if len(rest) < 2:
            sys.exit("usage: axon run <agent> <task> [grant...]")
        from .agent_loop import AgentLoop
        from .runtime import Runtime

        agent_name, task = rest[0], rest[1]
        # Least-privilege by default: a read-only set. Widen deliberately by
        # naming grants — never hand the agent a root-equivalent "*" implicitly.
        grants = rest[2:] or ["fs.read", "fs.list", "fs.search", "mem.*", "sys.info"]
        print(f"(agent granted: {' '.join(grants)})", file=sys.stderr)
        proc = Runtime(kernel).spawn(agent_name, grants)
        print(AgentLoop(kernel).run(proc, task))
        return 0

    sys.exit(f"unknown command: {cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
