"""Shared primitives for the BlackBox behavioral model.

Everything BlackBox learns is content-addressed: ids are derived from the
content they describe, so two independent runs over the same website produce
the same identifiers and models can be diffed, merged and versioned.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Iterable

SCHEMA_VERSION = 1


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utcnow().isoformat()


def stable_id(prefix: str, *parts: Any) -> str:
    """Deterministic short id derived from the given parts."""
    payload = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:12]}"


def content_hash(*parts: Any) -> str:
    payload = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


_WS = re.compile(r"\s+")


def normalize_text(value: str | None) -> str:
    """Lowercase, collapse whitespace, drop zero-width noise."""
    if not value:
        return ""
    text = value.replace("\u200b", "").replace("\ufeff", "")
    return _WS.sub(" ", text).strip().lower()


_TIMESTAMP_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b\d{1,2}:\d{2}(:\d{2})?\s*(am|pm)?\b",
        r"\b\d{4}-\d{2}-\d{2}t?[\d:.]*z?\b",
        r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},?\s*\d{0,4}\b",
        r"\b\d+\s*(second|minute|hour|day|week|month|year)s?\s+ago\b",
        r"\bjust now\b",
    )
)

_RANDOM_TOKEN = (
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE),
    re.compile(r"\b[0-9a-f]{16,}\b", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9+/]{24,}={0,2}\b"),
)

_LONG_NUMBER = re.compile(r"\b\d{6,}\b")


def scrub_volatile(text: str) -> str:
    """Remove differences that do not affect behavior.

    Timestamps, generated ids and long opaque tokens change on every render but
    say nothing about the application's behavior, so they are masked before
    fingerprinting and before state comparison.
    """
    value = text
    for pattern in _TIMESTAMP_PATTERNS:
        value = pattern.sub("<time>", value)
    for pattern in _RANDOM_TOKEN:
        value = pattern.sub("<token>", value)
    value = _LONG_NUMBER.sub("<num>", value)
    return value


def shingles(text: str, *, size: int = 3, limit: int = 400) -> list[str]:
    """Hashed word n-grams from normalized text, bounded and sorted for stability."""
    words = [w for w in normalize_text(scrub_volatile(text)).split(" ") if w]
    if not words:
        return []
    if len(words) < size:
        grams = [" ".join(words)]
    else:
        grams = [" ".join(words[i : i + size]) for i in range(len(words) - size + 1)]
    hashed = {hashlib.sha1(g.encode("utf-8")).hexdigest()[:12] for g in grams}
    return sorted(hashed)[:limit]


def jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    a, b = set(left), set(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class Timer:
    """Wall-clock timer used for latency metrics in experiments."""

    def __init__(self) -> None:
        self.started = time.perf_counter()

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started) * 1000.0

    def __enter__(self) -> "Timer":
        self.started = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> bool:
        return False
