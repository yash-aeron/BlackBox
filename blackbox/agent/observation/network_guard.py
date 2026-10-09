"""NetworkGuard: the API boundary, enforced in code rather than promised.

BlackBox is allowed to let the browser fetch whatever the page needs to render.
It is not allowed to *use* the network as a learning or execution channel.  This
module makes that structural:

* agent-issued JavaScript that calls ``fetch``/``XMLHttpRequest``/``WebSocket``
  is refused before it is evaluated;
* CDP commands that would expose response payloads (and therefore let an agent
  reconstruct a private API from traffic) are refused;
* observed traffic is counted, never captured - no URLs, headers or bodies are
  retained, so there is nothing to replay;
* every refusal is logged as evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from blackbox.browser.sandbox import origin_of


class NetworkAccessBlocked(RuntimeError):
    """Raised when the agent tries to step outside the browser interaction boundary."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"blocked network access: {reason}" + (f" ({detail})" if detail else ""))
        self.reason = reason
        self.detail = detail


# Direct request APIs an agent could use to bypass the UI.
REQUEST_API_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("fetch()", re.compile(r"(?<![\w.])fetch\s*\(", re.IGNORECASE)),
    ("XMLHttpRequest", re.compile(r"XMLHttpRequest", re.IGNORECASE)),
    ("WebSocket", re.compile(r"new\s+WebSocket", re.IGNORECASE)),
    ("EventSource", re.compile(r"new\s+EventSource", re.IGNORECASE)),
    ("sendBeacon", re.compile(r"sendBeacon\s*\(", re.IGNORECASE)),
    ("importScripts", re.compile(r"importScripts\s*\(", re.IGNORECASE)),
    ("navigator.serviceWorker", re.compile(r"serviceWorker\s*\.\s*register", re.IGNORECASE)),
)

# CDP commands that would hand the agent raw API traffic.
FORBIDDEN_CDP_METHODS: frozenset[str] = frozenset(
    {
        "Network.getResponseBody",
        "Network.getRequestPostData",
        "Network.getRequestPostDataAsync",
        "Network.searchInResponseBody",
        "Network.streamResourceContent",
        "Network.takeResponseBodyForInterceptionAsStream",
        "Fetch.getResponseBody",
        "Fetch.takeResponseBodyAsStream",
        "Fetch.continueRequest",
        "Fetch.fulfillRequest",
        "Network.setRequestInterception",
        "Network.emulateNetworkConditions",
        "Network.setExtraHTTPHeaders",
        "Network.setBlockedURLs",
        "Network.setUserAgentOverride",
    }
)


@dataclass
class NetworkGuard:
    """Watches the boundary and refuses to cross it."""

    allowed_origins: list[str]
    stats: dict[str, Any] = field(
        default_factory=lambda: {
            "observed_requests": 0,
            "observed_by_type": {},
            "blocked_agent_requests": 0,
            "blocked_commands": 0,
            "external_origins_seen": [],
        }
    )
    blocked: list[dict[str, Any]] = field(default_factory=list)
    evidence_sink: Any | None = None

    # -- observation (allowed, counted, not captured) ----------------------
    def note_observed_request(self, resource_type: str, url: str = "") -> None:
        self.stats["observed_requests"] += 1
        by_type = self.stats["observed_by_type"]
        by_type[resource_type] = by_type.get(resource_type, 0) + 1
        # Only the origin is retained, and only to prove the origin boundary held.
        if url.startswith("http"):
            origin = origin_of(url)
            known = {origin_of(o) if "://" in o else o.lower() for o in self.allowed_origins}
            if origin and origin not in known and origin not in self.stats["external_origins_seen"]:
                self.stats["external_origins_seen"].append(origin)

    # -- enforcement -------------------------------------------------------
    def check_expression(self, expression: str, *, purpose: str = "evaluate") -> None:
        """Refuse agent JavaScript that would talk to a backend directly."""
        if not expression:
            return
        for name, pattern in REQUEST_API_PATTERNS:
            if pattern.search(expression):
                self._record("agent_javascript", name, expression[:200], purpose)
                raise NetworkAccessBlocked(
                    f"agent JavaScript may not call {name}; the browser interaction boundary is the UI",
                    purpose,
                )

    def check_command(self, method: str) -> None:
        if method in FORBIDDEN_CDP_METHODS:
            self._record("cdp_command", method, "", "cdp")
            raise NetworkAccessBlocked(
                f"CDP method {method} would expose backend traffic and is not permitted",
                method,
            )

    def _record(self, kind: str, detail: str, snippet: str, purpose: str) -> None:
        entry = {"kind": kind, "detail": detail, "snippet": snippet, "purpose": purpose}
        self.blocked.append(entry)
        if kind == "cdp_command":
            self.stats["blocked_commands"] += 1
        else:
            self.stats["blocked_agent_requests"] += 1
        if self.evidence_sink is not None:
            self.evidence_sink(entry)

    # -- reporting ---------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        return {
            **self.stats,
            "blocked_recent": self.blocked[-25:],
            "policy": "the browser is the interaction boundary; APIs are never called directly",
        }
