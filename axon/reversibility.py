"""Reversibility — undo is a system primitive, not an afterthought.

The full OS will use copy-on-write snapshots (ZFS/btrfs). For the macOS layer
we demonstrate the same contract at file/directory granularity: before any
mutating action runs, the kernel snapshots the prior state of each path it
touches into a journal so the action can be rolled back. A rogue or mistaken
agent action is always undoable.

One audit id can cover several paths (e.g. ``fs.move`` snapshots both ``src``
and ``dst``), so the journal keys a *list* of snapshots per audit id, and undo
restores them in reverse order.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any


class Journal:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.snap_dir = home / "snapshots"
        self.snap_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = home / "journal.json"

    def _index(self) -> dict[str, list[dict[str, Any]]]:
        if self.index_path.exists():
            return json.loads(self.index_path.read_text())
        return {}

    def _write_index(self, idx: dict[str, list[dict[str, Any]]]) -> None:
        self.index_path.write_text(json.dumps(idx, indent=2))

    def snapshot_file(self, audit_id: str, path: str) -> None:
        """Capture the pre-action state of `path` (file or directory) under
        this audit entry. Appends, so multiple paths can share one audit id."""
        p = Path(path).expanduser()
        entry: dict[str, Any] = {
            "path": str(p),
            "existed": False,
            "is_dir": False,
            "snapshot": None,
        }
        if p.exists():
            snap = self.snap_dir / uuid.uuid4().hex
            if p.is_dir() and not p.is_symlink():
                shutil.copytree(p, snap)
                entry.update(existed=True, is_dir=True, snapshot=str(snap))
            else:
                shutil.copy2(p, snap)
                entry.update(existed=True, is_dir=False, snapshot=str(snap))
        idx = self._index()
        idx.setdefault(audit_id, []).append(entry)
        self._write_index(idx)

    def undo(self, audit_id: str) -> str:
        """Restore every path captured for `audit_id` (reverse order)."""
        idx = self._index()
        if audit_id not in idx:
            raise KeyError(f"nothing to undo for {audit_id}")
        results = []
        for rec in reversed(idx[audit_id]):
            p = Path(rec["path"])
            if rec["existed"]:
                if rec.get("is_dir"):
                    if p.exists():
                        shutil.rmtree(p)
                    shutil.copytree(rec["snapshot"], p)
                    results.append(f"restored directory {p}")
                else:
                    shutil.copy2(rec["snapshot"], p)
                    results.append(f"restored {p}")
            else:
                # The path did not exist before the action — remove what it made.
                if p.is_dir() and not p.is_symlink():
                    shutil.rmtree(p)
                    results.append(f"removed directory {p}")
                elif p.exists() or p.is_symlink():
                    p.unlink()
                    results.append(f"removed {p}")
                else:
                    results.append(f"nothing to remove at {p}")
        del idx[audit_id]
        self._write_index(idx)
        return "; ".join(results)
