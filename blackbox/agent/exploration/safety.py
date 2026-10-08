"""Safety classification and the human-in-the-loop gate.

Risk is decided from what a user would understand the control to do - its
visible label, its role, the dialog it sits in, and what has already happened -
never from optimism about the demo.  Default policies make LOW actions
autonomous, and require explicit configuration or a human decision for anything
that creates, changes, deletes, sends, publishes or pays.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from ..model.action import Action, ActionType, RiskLevel
from ..model.base import normalize_text
from ..model.element import Element, ElementRole
from ..observation.observation import Observation


class ExecutionMode(str, Enum):
    SAFE = "SAFE"
    SUPERVISED = "SUPERVISED"
    AUTONOMOUS = "AUTONOMOUS"


CRITICAL_PATTERNS = (
    re.compile(r"\b(pay|purchase|buy now|place order|checkout and pay|subscribe|billing|credit card|card number)\b", re.I),
    re.compile(r"\b(change password|reset password|new password|delete account|close account|deactivate)\b", re.I),
    re.compile(r"\b(transfer|withdraw|refund|wire)\b", re.I),
)
HIGH_PATTERNS = (
    re.compile(r"\b(delete|remove|erase|destroy|discard|revoke|unlink)\b", re.I),
    re.compile(r"\b(publish|go live|deploy|release|post publicly)\b", re.I),
    re.compile(r"\b(send|email|notify|invite|share|message)\b", re.I),
    re.compile(r"\b(confirm|yes.*delete|delete.*confirm)\b", re.I),
)
MEDIUM_PATTERNS = (
    re.compile(r"\b(save|create|add|update|edit|submit|apply|upload|import|export|download|place|settings)\b", re.I),
    re.compile(r"\b(save report|apply filters|empty cart)\b", re.I),
)
LOW_PATTERNS = (
    re.compile(r"\b(view|open|show|details|next|previous|back|tab|menu|filter|search|sort|cancel|close|expand|collapse|help)\b", re.I),
)

DESTRUCTIVE_KEYWORDS = (
    "delete",
    "remove",
    "erase",
    "format",
    "purchase",
    "pay",
    "send",
    "publish",
    "confirm",
    "reset",
    "cancel subscription",
    "deactivate",
    "close account",
)


@dataclass
class RiskAssessment:
    risk: RiskLevel
    reasons: list[str] = field(default_factory=list)
    destructive: bool = False
    in_confirmation_dialog: bool = False

    def describe(self) -> str:
        return f"{self.risk.value}: " + "; ".join(self.reasons[:3])


class SafetyClassifier:
    """Assigns a risk level to a proposed action with reasons attached."""

    def classify(
        self,
        action: Action,
        *,
        element: Element | None = None,
        observation: Observation | None = None,
        history: list[str] | None = None,
    ) -> RiskAssessment:
        reasons: list[str] = []
        label = ""
        if element is not None:
            label = f"{element.label()} {element.attributes.get('__dialog_title', '')}"
        elif action.target is not None:
            label = action.target.label()
        label = f"{label} {' '.join(str(v)[:40] for v in action.parameters.values() if isinstance(v, str))}"
        text = normalize_text(label)

        in_confirm = False
        if observation is not None and observation.dialogs:
            dialog_text = normalize_text(" ".join(d.text + " " + d.title for d in observation.dialogs))
            in_confirm = any(keyword in dialog_text for keyword in ("cannot be undone", "are you sure", "confirm"))
            if in_confirm:
                reasons.append("action is inside a confirmation dialog")

        if action.type is ActionType.NAVIGATE:
            risk = RiskLevel.LOW
            reasons.append("navigation stays inside the authorized origin")
        elif action.type in (ActionType.SCROLL, ActionType.WAIT_FOR_STATE, ActionType.CLOSE_DIALOG, ActionType.NAVIGATE_BACK):
            risk = RiskLevel.LOW
            reasons.append("non-mutating browser action")
        elif action.type in (
            ActionType.TYPE,
            ActionType.SELECT,
            ActionType.CHECK,
            ActionType.UNCHECK,
            ActionType.CLEAR,
        ):
            # Entering data into a field is a browser-local act.  It matters what
            # the *field* is, not what words appear in its label: typing an
            # address into an "Email" field is not sending an email.
            risk, field_reasons = self._field_entry_risk(element, label)
            reasons.extend(field_reasons)
        elif action.type is ActionType.UPLOAD:
            risk = RiskLevel.MEDIUM
            reasons.append("uploading a file is a mutating action")
        elif action.type is ActionType.DOWNLOAD:
            risk = RiskLevel.MEDIUM
            reasons.append("triggering a download")
        else:
            risk = RiskLevel.LOW
            for pattern in CRITICAL_PATTERNS:
                if pattern.search(text):
                    risk = RiskLevel.CRITICAL
                    reasons.append(f"critical pattern {pattern.pattern!r}")
                    break
            if risk is not RiskLevel.CRITICAL:
                for pattern in HIGH_PATTERNS:
                    if pattern.search(text):
                        risk = RiskLevel.HIGH
                        reasons.append(f"high-risk pattern {pattern.pattern!r}")
                        break
            if risk is RiskLevel.LOW:
                for pattern in MEDIUM_PATTERNS:
                    if pattern.search(text):
                        risk = RiskLevel.MEDIUM
                        reasons.append(f"mutating pattern {pattern.pattern!r}")
                        break
            if risk is RiskLevel.LOW:
                for pattern in LOW_PATTERNS:
                    if pattern.search(text):
                        reasons.append(f"read-only pattern {pattern.pattern!r}")
                        break
            if risk is RiskLevel.LOW and not reasons:
                reasons.append("no destructive or mutating signal in the control's meaning")

        destructive = any(word in text for word in DESTRUCTIVE_KEYWORDS)

        # A second destructive click inside a confirmation dialog is the moment
        # that actually destroys data, so it can never be treated as LOW.
        if in_confirm and destructive:
            risk = RiskLevel.CRITICAL
            reasons.append("destructive confirmation: this is the click that mutates data")
        elif in_confirm and risk is RiskLevel.LOW:
            risk = RiskLevel.MEDIUM
            reasons.append("confirming a dialog is at least a mutating action")

        if history:
            recent = [entry for entry in history[-4:]]
            if any("delete" in normalize_text(entry) for entry in recent) and destructive:
                risk = RiskLevel.CRITICAL
                reasons.append("part of a destructive sequence observed just before")

        return RiskAssessment(risk=risk, reasons=reasons, destructive=destructive, in_confirmation_dialog=in_confirm)

    def is_destructive_label(self, label: str) -> bool:
        text = normalize_text(label)
        return any(word in text for word in DESTRUCTIVE_KEYWORDS)

    def _field_entry_risk(self, element: Element | None, label: str) -> tuple[RiskLevel, list[str]]:
        """Risk of putting a value into a field, judged by the field's meaning."""
        probe = normalize_text(label)
        if element is not None and element.semantic_role is ElementRole.PASSWORD_FIELD:
            return RiskLevel.HIGH, ["field is a password input; values are never stored"]
        if any(
            keyword in probe
            for keyword in ("card number", "cvc", "cvv", "security code", "expiry", "ssn", "social security", "iban")
        ):
            return RiskLevel.HIGH, ["field carries payment or identity credentials"]
        if element is not None and element.semantic_role is ElementRole.CHECKBOX and "agree" in probe:
            return RiskLevel.MEDIUM, ["accepting terms or consent"]
        return RiskLevel.LOW, ["entering data into a form field"]


@dataclass
class SafetyPolicy:
    """Which risks may proceed without a human."""

    mode: ExecutionMode = ExecutionMode.SUPERVISED
    autonomous_risk: RiskLevel = RiskLevel.LOW
    allow_medium_with_config: bool = False
    allow_high_with_config: bool = False
    allow_critical_with_config: bool = False

    def permits(self, risk: RiskLevel) -> bool:
        if self.mode is ExecutionMode.SAFE:
            return risk is RiskLevel.LOW
        if self.mode is ExecutionMode.AUTONOMOUS:
            if risk.rank <= RiskLevel.MEDIUM.rank:
                return True
            if risk is RiskLevel.HIGH:
                return self.allow_high_with_config
            return self.allow_critical_with_config
        # SUPERVISED
        if risk.rank <= self.autonomous_risk.rank:
            return True
        if risk is RiskLevel.MEDIUM:
            return self.allow_medium_with_config
        if risk is RiskLevel.HIGH:
            return self.allow_high_with_config
        return self.allow_critical_with_config

    def describe(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "autonomous_risk": self.autonomous_risk.value,
            "allow_medium_with_config": self.allow_medium_with_config,
            "allow_high_with_config": self.allow_high_with_config,
            "allow_critical_with_config": self.allow_critical_with_config,
        }


@dataclass
class ApprovalRequest:
    request_id: str
    action: str
    target: str
    reason: str
    expected_effect: str
    risk: str
    evidence: list[str] = field(default_factory=list)
    created_at: float = 0.0


class ApprovalBroker:
    """Queues risky actions for a human, and honours their decision.

    In SUPERVISED mode an action above the autonomous threshold blocks until the
    decision arrives (from the API/dashboard) or the timeout expires, in which
    case the safe answer - rejection - is used.
    """

    def __init__(self, *, timeout_seconds: float = 120.0, auto_reject: bool = True, unattended: bool = False) -> None:
        self.timeout_seconds = timeout_seconds
        self.auto_reject = auto_reject
        # Unattended runs have nobody to ask: risky actions are refused
        # immediately and recorded, instead of blocking the loop on a timeout.
        self.unattended = unattended
        self.pending: dict[str, ApprovalRequest] = {}
        self.decisions: dict[str, str] = {}
        self.history: list[dict[str, Any]] = []
        self._events: dict[str, asyncio.Event] = {}
        self._counter = 0
        self.listeners: list[Callable[[ApprovalRequest], None]] = []

    def _next_id(self) -> str:
        self._counter += 1
        return f"approval-{self._counter:04d}"

    async def request(self, request: ApprovalRequest) -> bool:
        import time

        request.request_id = request.request_id or self._next_id()
        request.created_at = request.created_at or time.time()
        self.pending[request.request_id] = request
        event = asyncio.Event()
        self._events[request.request_id] = event
        for listener in self.listeners:
            try:
                listener(request)
            except Exception:  # noqa: BLE001 - listeners must not break the loop
                pass
        if self.unattended:
            decision = "REJECT"
            self.pending.pop(request.request_id, None)
            self._events.pop(request.request_id, None)
            self.history.append({"request": request, "decision": decision, "reason": "unattended run"})
            return False
        try:
            await asyncio.wait_for(event.wait(), timeout=self.timeout_seconds)
            decision = self.decisions.get(request.request_id, "REJECT")
        except asyncio.TimeoutError:
            decision = "REJECT" if self.auto_reject else "APPROVE"
        self.pending.pop(request.request_id, None)
        self._events.pop(request.request_id, None)
        self.history.append({"request": request, "decision": decision})
        return decision == "APPROVE"

    def resolve(self, request_id: str, decision: str) -> bool:
        if request_id not in self.pending:
            return False
        self.decisions[request_id] = decision.upper()
        event = self._events.get(request_id)
        if event:
            event.set()
        return True

    def pause_all(self) -> None:
        for event in self._events.values():
            event.clear()

    def summary(self) -> dict[str, Any]:
        return {
            "pending": [
                {
                    "request_id": r.request_id,
                    "action": r.action,
                    "target": r.target,
                    "risk": r.risk,
                    "reason": r.reason,
                }
                for r in self.pending.values()
            ],
            "decisions": len(self.history),
            "approved": sum(1 for entry in self.history if entry["decision"] == "APPROVE"),
            "rejected": sum(1 for entry in self.history if entry["decision"] == "REJECT"),
        }
