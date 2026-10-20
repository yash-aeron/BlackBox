"""Print a human-readable report of a learned model from its database.

Usage:
    python tools/model_report.py --db artifacts/blackbox.db --target demo_crm [--json]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def load(db_path: str, target: str) -> dict:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row

    def rows(sql: str, params: tuple = ()) -> list[dict]:
        return [dict(row) for row in connection.execute(sql, params).fetchall()]

    def count(table: str) -> int:
        row = connection.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE target_id = ?", (target,)).fetchone()
        return int(row["n"]) if row else 0

    states = rows(
        "SELECT state_id, url, url_key, title, page_identity, status, confidence, visit_count, semantic_summary "
        "FROM states WHERE target_id = ? ORDER BY visit_count DESC, state_id",
        (target,),
    )
    transitions = rows(
        "SELECT transition_id, source_state, target_state, action_signature, action_type, risk, status, confidence, "
        "execution_count, success_count, failure_count FROM transitions WHERE target_id = ? "
        "ORDER BY status DESC, success_count DESC",
        (target,),
    )
    workflows = rows(
        "SELECT workflow_id, name, goal, expected_end_state, confidence, verification_count FROM workflows "
        "WHERE target_id = ? ORDER BY confidence DESC",
        (target,),
    )
    constraints = rows(
        "SELECT constraint_id, kind, subject, expression, message, status, confidence FROM constraints "
        "WHERE target_id = ? ORDER BY confidence DESC",
        (target,),
    )
    hypotheses = rows(
        "SELECT hypothesis_id, statement, status, confidence FROM hypotheses WHERE target_id = ? "
        "ORDER BY confidence DESC",
        (target,),
    )
    evidence = rows(
        "SELECT evidence_id, kind, transition_id, message, created_at FROM evidence WHERE target_id = ? "
        "ORDER BY created_at DESC LIMIT 30",
        (target,),
    )
    experiments = rows(
        "SELECT experiment_id, kind, status, metrics, started_at, finished_at FROM experiments "
        "WHERE target_id = ? ORDER BY started_at DESC LIMIT 10",
        (target,),
    )
    pages = rows(
        "SELECT page_id, name, url_pattern, section FROM pages WHERE target_id = ?", (target,)
    )
    model_versions = rows(
        "SELECT version, created_at, browser_version, parent_version FROM models WHERE target_id = ? "
        "ORDER BY version",
        (target,),
    )
    return {
        "target": target,
        "counts": {
            "states": count("states"),
            "transitions": count("transitions"),
            "verified_transitions": connection.execute(
                "SELECT COUNT(*) AS n FROM transitions WHERE target_id = ? AND status = 'VERIFIED'", (target,)
            ).fetchone()["n"],
            "workflows": count("workflows"),
            "constraints": count("constraints"),
            "hypotheses": count("hypotheses"),
            "evidence": count("evidence"),
            "experiments": count("experiments"),
            "pages": count("pages"),
        },
        "states": states,
        "transitions": transitions,
        "workflows": workflows,
        "constraints": constraints,
        "hypotheses": hypotheses,
        "evidence": evidence,
        "experiments": experiments,
        "pages": pages,
        "model_versions": model_versions,
    }


def render(report: dict) -> str:
    lines: list[str] = []
    counts = report["counts"]
    lines.append(f"== model report: {report['target']} ==")
    lines.append(
        "states={states} transitions={transitions} verified={verified_transitions} workflows={workflows} "
        "constraints={constraints} hypotheses={hypotheses} evidence={evidence} pages={pages} experiments={experiments}".format(
            **counts
        )
    )
    lines.append("")
    lines.append(f"-- pages ({len(report['pages'])}) --")
    for page in report["pages"]:
        lines.append(f"  {page['name'][:40]:42s} {page['url_pattern']}")
    lines.append("")
    lines.append(f"-- states ({len(report['states'])}) --")
    for state in report["states"]:
        lines.append(
            f"  {state['state_id']:16s} v{state['visit_count']:<3d} {state['status']:8s} "
            f"{(state['title'] or '')[:28]:30s} {state['url_key'][:52]}"
        )
        if state["semantic_summary"]:
            lines.append(f"      {state['semantic_summary'][:150]}")
    lines.append("")
    lines.append(f"-- transitions ({len(report['transitions'])}) --")
    for transition in report["transitions"][:60]:
        lines.append(
            f"  [{transition['status']:9s} c={transition['confidence']:.2f} "
            f"ok={transition['success_count']}/{transition['execution_count']}] "
            f"{transition['source_state']} --{transition['action_signature'][:60]}--> {transition['target_state']}"
        )
    lines.append("")
    lines.append(f"-- workflows ({len(report['workflows'])}) --")
    for workflow in report["workflows"]:
        lines.append(
            f"  {workflow['goal']:26s} steps=? conf={workflow['confidence']:.2f} "
            f"verified={workflow['verification_count']} -> {workflow['expected_end_state'][:60]}"
        )
    lines.append("")
    lines.append(f"-- constraints ({len(report['constraints'])}) --")
    for constraint in report["constraints"][:25]:
        lines.append(
            f"  [{constraint['status']:8s} c={constraint['confidence']:.2f}] {constraint['subject'][:40]}: "
            f"{constraint['expression'][:80]}"
        )
    lines.append("")
    lines.append(f"-- hypotheses ({len(report['hypotheses'])}) --")
    for hypothesis in report["hypotheses"][:25]:
        lines.append(f"  [{hypothesis['status']:8s} c={hypothesis['confidence']:.2f}] {hypothesis['statement'][:110]}")
    lines.append("")
    lines.append(f"-- recent evidence ({len(report['evidence'])}) --")
    for item in report["evidence"][:12]:
        lines.append(f"  [{item['kind']:18s}] {str(item['message'])[:110]}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="artifacts/blackbox.db")
    parser.add_argument("--target", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", default=None, help="write the report to this file")
    args = parser.parse_args()
    if not Path(args.db).exists():
        print(f"database not found: {args.db}", file=sys.stderr)
        return 1
    report = load(args.db, args.target)
    text = json.dumps(report, indent=2, default=str) if args.json else render(report)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
