"""List learned transitions (and optionally constraints) for a target.

Usage:
    python tools/list_transitions.py --target demo_forms [--status REFUTED] [--limit 40]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="artifacts/blackbox.db")
    parser.add_argument("--target", required=True)
    parser.add_argument("--status", default=None)
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--data-entry-only", action="store_true")
    args = parser.parse_args()
    if not Path(args.db).exists():
        print(f"no database at {args.db}", file=sys.stderr)
        return 1
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT payload FROM transitions WHERE target_id = ? ORDER BY status, action_type", (args.target,)
    ).fetchall()

    printed = 0
    for row in rows:
        transition = json.loads(row["payload"])
        if args.status and transition["status"] != args.status:
            continue
        action = transition["action"]
        if args.data_entry_only and action["type"] not in ("TYPE", "SELECT", "CHECK", "UNCHECK", "UPLOAD"):
            continue
        target = (action.get("target") or {}).get("name") or ""
        value = action.get("parameters", {}).get("value") or action.get("parameters", {}).get("option") or ""
        kinds = [effect["kind"] for effect in transition.get("observed_effects", [])][:4]
        print(
            f"[{transition['status']:9s}] {action['type']:8s} {target[:34]:36s} {str(value)[:24]:26s} "
            f"{transition['source_state']} -> {transition['target_state']} {kinds}"
        )
        printed += 1
        if printed >= args.limit:
            break
    print(f"\n{printed} transition(s) shown for {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
