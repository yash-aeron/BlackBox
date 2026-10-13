"""Origin sandbox: the boundary that makes "authorized target" enforceable.

Every navigation and every action that could move the browser is checked here
before it reaches CDP.  Anything outside ``allowed_origins`` is blocked, logged,
and can optionally be surfaced to a human for explicit approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

SCHEME_ALLOWLIST = ("http", "https")
INTERNAL_SCHEMES = ("about:", "data:", "blob:", "javascript:")


class OriginViolation(RuntimeError):
    """Raised when an operation would leave the authorized origin set."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"origin violation for {url!r}: {reason}")
        self.url = url
        self.reason = reason


def origin_of(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https"):
        host = (parsed.hostname or "").lower()
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host}{port}"
    return parsed.scheme or ""


@dataclass
class NavigationDecision:
    allowed: bool
    reason: str = ""
    requires_approval: bool = False
    url: str = ""


@dataclass
class Sandbox:
    """Decides whether the browser is allowed to go somewhere."""

    allowed_origins: list[str]
    allow_subdomains: bool = False
    approval_callback: Any | None = None
    blocked_log: list[dict[str, Any]] = field(default_factory=list)
    _allowed: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        self._allowed = {origin_of(o) if "://" in o else o.lower() for o in self.allowed_origins}

    def is_allowed(self, url: str) -> NavigationDecision:
        if not url:
            return NavigationDecision(True, "empty url (no-op)", url=url)
        lowered = url.lower()
        if lowered.startswith(INTERNAL_SCHEMES):
            # about:blank, data: and blob: do not contact any server.
            return NavigationDecision(True, "internal scheme", url=url)

        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        if scheme not in SCHEME_ALLOWLIST:
            return NavigationDecision(False, f"scheme {scheme or 'unknown'} is not http(s)", url=url)

        origin = origin_of(url)
        if origin in self._allowed:
            return NavigationDecision(True, "origin allowed", url=url)

        if self.allow_subdomains:
            host = (parsed.hostname or "").lower()
            for allowed in self._allowed:
                allowed_host = urlparse(allowed).hostname or ""
                if allowed_host and host.endswith("." + allowed_host):
                    return NavigationDecision(True, "subdomain of allowed origin", url=url)

        return NavigationDecision(False, f"origin {origin or 'unknown'} is not registered for this target", url=url)

    def check(self, url: str, *, kind: str = "navigation", allow_approval: bool = False) -> NavigationDecision:
        """Decide, log and (optionally) escalate a URL before it is visited."""
        decision = self.is_allowed(url)
        if decision.allowed:
            return decision

        decision.requires_approval = allow_approval and self.approval_callback is not None
        self.blocked_log.append(
            {
                "kind": kind,
                "url": url,
                "origin": origin_of(url),
                "reason": decision.reason,
                "requires_approval": decision.requires_approval,
            }
        )
        if decision.requires_approval:
            approved = self.approval_callback({"kind": kind, "url": url, "reason": decision.reason})
            if approved:
                decision.allowed = True
                decision.reason = "explicitly approved by a human for this navigation"
                self.blocked_log[-1]["approved"] = True
        return decision

    def assert_allowed(self, url: str, *, kind: str = "navigation") -> None:
        decision = self.check(url, kind=kind)
        if not decision.allowed:
            raise OriginViolation(url, decision.reason)

    def assert_current_url(self, url: str) -> None:
        """Used after the page moves on its own (redirect, script navigation)."""
        decision = self.is_allowed(url)
        if not decision.allowed:
            self.blocked_log.append(
                {
                    "kind": "unexpected_navigation",
                    "url": url,
                    "origin": origin_of(url),
                    "reason": decision.reason,
                    "requires_approval": False,
                }
            )
            raise OriginViolation(url, f"page navigated outside the authorized origins: {decision.reason}")

    def snapshot(self) -> dict[str, Any]:
        return {
            "allowed_origins": sorted(self._allowed),
            "blocked_count": len(self.blocked_log),
            "blocked": self.blocked_log[-50:],
        }
