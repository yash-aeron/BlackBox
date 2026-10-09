"""One-off patch: harden the dashboard's esc()/truncate() helpers.

The served app.js threw "Cannot access 'value' before initialization" from
truncate() while rendering the state graph.  Both helpers are rewritten to avoid
the intermediate binding and to cope with non-string inputs and bad limits.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TARGET = Path("blackbox/api/static/app.js")

REPLACEMENT = '''function esc(input) {
  const text = input === null || input === undefined ? "" : String(input);
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function truncate(input, limit) {
  const text = input === null || input === undefined ? "" : String(input);
  const max = Number(limit);
  if (!Number.isFinite(max) || max <= 0 || text.length <= max) return text;
  return max <= 1 ? "\\u2026" : text.slice(0, max - 1) + "\\u2026";
}'''

ESC = re.compile(r"function esc\(value\)\s*\{.*?\n\}", re.DOTALL)
TRUNC = re.compile(r"function truncate\(text, limit\)\s*\{.*?\n\}", re.DOTALL)


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    esc_match = ESC.search(source)
    trunc_match = TRUNC.search(source)
    if not esc_match or not trunc_match:
        print("could not locate the helpers; no change made", file=sys.stderr)
        return 1
    # Replace truncate first so the offsets of esc do not matter.
    source = source[: trunc_match.start()] + REPLACEMENT.split("\n\n", 1)[1] + source[trunc_match.end() :]
    esc_match = ESC.search(source)
    source = source[: esc_match.start()] + REPLACEMENT.split("\n\n", 1)[0] + source[esc_match.end() :]
    TARGET.write_text(source, encoding="utf-8")
    print("patched", TARGET)
    for index, line in enumerate(source.splitlines()[8:34], start=9):
        print(f"{index:4d}: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
