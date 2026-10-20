"""Print a compact summary of every learned model in the database."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

TABLES = ("states", "transitions", "workflows", "constraints", "hypotheses", "evidence", "experiments")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="artifacts/blackbox.db")
    args = parser.parse_args()
    if not Path(args.db).exists():
        print(f"no database at {args.db}", file=sys.stderr)
        return 1
    connection = sqlite3.connect(args.db)
    targets = [row[0] for row in connection.execute("SELECT DISTINCT target_id FROM states ORDER BY target_id")]
    print(f"{'target':24s} " + " ".join(f"{name[:9]:>9s}" for name in TABLES) + "  verified")
    for target in targets:
        counts = []
        for table in TABLES:
            row = connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE target_id = ?", (target,)
            ).fetchone()
            counts.append(row[0])
        verified = connection.execute(
            "SELECT COUNT(*) FROM transitions WHERE target_id = ? AND status = 'VERIFIED'", (target,)
        ).fetchone()[0]
        print(f"{target:24s} " + " ".join(f"{count:9d}" for count in counts) + f"  {verified:8d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
