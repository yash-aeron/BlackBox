"""Show mined workflows with their steps, plus the transitions carrying success effects."""

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
    args = parser.parse_args()
    if not Path(args.db).exists():
        print(f"no database at {args.db}", file=sys.stderr)
        return 1
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row

    print("== workflows ==")
    for row in connection.execute(
        "SELECT payload FROM workflows WHERE target_id = ? ORDER BY confidence DESC", (args.target,)
    ):
        workflow = json.loads(row["payload"])
        print(f"\n* {workflow['goal']}  ({workflow['name']})  conf={workflow['confidence']} verified={workflow['verification_count']}")
        print(f"  start={workflow['start_state_id']}  params={[p['name'] + ':' + p['kind'] for p in workflow['parameters']]}")
        for step in workflow["steps"]:
            params = {k: v for k, v in (step["action"].get("parameters") or {}).items() if k != "file"}
            print(f"    {step['index']:2d}. {step['action']['type']:8s} {step['description'][:78]:80s} {params}")

    print("\n== transitions with success/data effects ==")
    for row in connection.execute(
        "SELECT payload FROM transitions WHERE target_id = ? AND status != 'REFUTED'", (args.target,)
    ):
        transition = json.loads(row["payload"])
        kinds = [effect["kind"] for effect in transition.get("observed_effects", [])]
        if any(kind in ("SUCCESS_MESSAGE", "DATA_CHANGED") for kind in kinds):
            print(
                f"  [{transition['status']:8s}] {transition['source_state']} --"
                f"{transition['action']['type']} {((transition['action'].get('target') or {}).get('name') or '')[:34]}--> "
                f"{transition['target_state']}  {kinds}"
            )
    print("\n== constraints ==")
    for row in connection.execute(
        "SELECT payload FROM constraints WHERE target_id = ?", (args.target,)
    ):
        constraint = json.loads(row["payload"])
        print(f"  [{constraint['status']}] {constraint['subject'][:50]}: {constraint['expression'][:70]} :: {str(constraint.get('message'))[:80]}")
    print("\n== hypotheses ==")
    for row in connection.execute(
        "SELECT payload FROM hypotheses WHERE target_id = ?", (args.target,)
    ):
        hypothesis = json.loads(row["payload"])
        print(f"  [{hypothesis['status']} c={hypothesis['confidence']:.2f}] {hypothesis['statement'][:110]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
