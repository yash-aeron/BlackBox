"""Print one task's full result from a benchmark report, including predicate evidence.

Usage:
    python tools/show_task_result.py --task crm_create_customer [--report <path>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def latest_report(directory: Path) -> Path:
    reports = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not reports:
        raise SystemExit(f"no reports in {directory}")
    return reports[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default=None)
    parser.add_argument("--task", required=True)
    parser.add_argument("--dir", default="artifacts/benchmarks")
    args = parser.parse_args()

    path = Path(args.report) if args.report else latest_report(Path(args.dir))
    payload = json.loads(path.read_text(encoding="utf-8"))
    print(f"report: {path}  mode={payload.get('mode')}  target={payload.get('target')}")
    tasks = payload.get("metrics", {}).get("per_task", [])
    entry = next((task for task in tasks if task["task_id"] == args.task), None)
    if entry is None:
        print(f"task {args.task!r} not in this report; available: {[t['task_id'] for t in tasks]}")
        return 1

    print(f"\n== {entry['task_id']} ==")
    print(f"success={entry['success']}  plan_source={entry['plan_source']}  actions={entry['actions']}  "
          f"steps={entry['steps_verified']}/{entry['steps_total']}  reuse={entry['model_reuse']}  "
          f"workflow={entry['used_workflow']}  exploration={entry['used_exploration']}")
    for note in entry.get("notes", []):
        print(f"  note: {note}")

    plan = entry.get("plan") or {}
    if plan:
        print(f"\n  plan: source={plan.get('source')} workflow={plan.get('workflow_id')} "
              f"confidence={plan.get('confidence')} steps={len(plan.get('steps', []))}")
        for step in plan.get("steps", []):
            params = {k: v for k, v in (step.get("parameters") or {}).items() if k != "file"}
            print(f"    {step.get('index'):2d}. {step.get('action_type'):8s} {str(step.get('description'))[:70]:72s} {params}")

    verification = entry.get("verification") or {}
    predicates = verification.get("predicates") or []
    if predicates:
        print("\n  success conditions:")
        for predicate in predicates:
            mark = "OK  " if predicate.get("satisfied") else "MISS"
            print(
                f"    [{mark}] {predicate.get('kind')}({predicate.get('value')!r}) "
                f"evidence: {str(predicate.get('evidence'))[:90]}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
