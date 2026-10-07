"""Recovery: what to do when reality disagrees with the model.

The policy is deliberately boring and bounded:

    OBSERVE -> COMPARE -> REASSESS -> RETRY OR ALTERNATE ACTION -> REPLAN -> SAFE STOP

There is no infinite retry.  Every decision is recorded so a run can be audited
afterwards, and unsafe situations escalate instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..model.action import ActionResult


class FailureKind(str, Enum):
    LOCATOR_FAILURE = "LOCATOR_FAILURE"
    ELEMENT_DISABLED = "ELEMENT_DISABLED"
    NO_EFFECT = "NO_EFFECT"
    UNEXPECTED_DIALOG = "UNEXPECTED_DIALOG"
    NAVIGATION_FAILURE = "NAVIGATION_FAILURE"
    ORIGIN_BLOCKED = "ORIGIN_BLOCKED"
    TIMEOUT = "TIMEOUT"
    STATE_MISMATCH = "STATE_MISMATCH"
    BROWSER_CRASH = "BROWSER_CRASH"
    VALIDATION_BLOCKED = "VALIDATION_BLOCKED"
    STALE_WORKFLOW = "STALE_WORKFLOW"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    IMPOSSIBLE_TASK = "IMPOSSIBLE_TASK"
    UNKNOWN = "UNKNOWN"


class RecoveryStrategy(str, Enum):
    RETRY = "RETRY"
    REGENERATE_LOCATOR = "REGENERATE_LOCATOR"
    ALTERNATE_LOCATOR = "ALTERNATE_LOCATOR"
    RE_OBSERVE = "RE_OBSERVE"
    RETURN_TO_KNOWN_STATE = "RETURN_TO_KNOWN_STATE"
    ALTERNATE_WORKFLOW = "ALTERNATE_WORKFLOW"
    TARGETED_EXPLORATION = "TARGETED_EXPLORATION"
    RESTART_BROWSER = "RESTART_BROWSER"
    ESCALATE_TO_HUMAN = "ESCALATE_TO_HUMAN"
    SAFE_STOP = "SAFE_STOP"


@dataclass
class RecoveryDecision:
    strategy: RecoveryStrategy
    failure: FailureKind
    reason: str
    max_attempts: int = 1
    requires_human: bool = False
    terminal: bool = False


@dataclass
class RecoveryManager:
    """Bounded, explainable failure handling."""

    max_retries_per_action: int = 2
    max_locator_regenerations: int = 2
    max_restarts: int = 1
    max_recoveries_per_task: int = 6
    recoveries: int = 0
    history: list[dict[str, object]] = field(default_factory=list)

    # -- classification ----------------------------------------------------
    def classify(self, result: ActionResult | None, *, diff_no_effect: bool = False, error: str | None = None) -> FailureKind:
        message = (error or (result.error if result else "") or "").lower()
        if "origin violation" in message or "not registered for this target" in message:
            return FailureKind.ORIGIN_BLOCKED
        if "browser" in message and ("crash" in message or "closed" in message or "lost" in message):
            return FailureKind.BROWSER_CRASH
        if result is not None:
            if result.status == "BLOCKED":
                return FailureKind.ELEMENT_DISABLED
            if "locator" in message or "not found" in message or "no locator" in message:
                return FailureKind.LOCATOR_FAILURE
            if "timed out" in message or "timeout" in message:
                return FailureKind.TIMEOUT
            if "navigation" in message:
                return FailureKind.NAVIGATION_FAILURE
        if diff_no_effect:
            return FailureKind.NO_EFFECT
        return FailureKind.UNKNOWN

    # -- policy ------------------------------------------------------------
    def decide(
        self,
        failure: FailureKind,
        *,
        attempt: int = 1,
        locator_attempts: int = 0,
        alternates_available: bool = False,
        replan_available: bool = False,
        known_state_available: bool = False,
        validation_message: str | None = None,
        confidence: float = 1.0,
    ) -> RecoveryDecision:
        self.recoveries += 1
        if self.recoveries > self.max_recoveries_per_task:
            return RecoveryDecision(
                RecoveryStrategy.SAFE_STOP,
                failure,
                "recovery budget exhausted for this task",
                terminal=True,
            )

        if failure is FailureKind.ORIGIN_BLOCKED:
            return RecoveryDecision(
                RecoveryStrategy.SAFE_STOP,
                failure,
                "navigation left the authorized origins; refusing to continue",
                terminal=True,
            )

        if failure is FailureKind.BROWSER_CRASH:
            if self.max_restarts > 0:
                self.max_restarts -= 1
                return RecoveryDecision(
                    RecoveryStrategy.RESTART_BROWSER,
                    failure,
                    "browser died; rebuild it and resume from the last known state",
                )
            return RecoveryDecision(
                RecoveryStrategy.SAFE_STOP, failure, "browser crashed and no restarts remain", terminal=True
            )

        if failure is FailureKind.LOCATOR_FAILURE:
            if locator_attempts < self.max_locator_regenerations:
                return RecoveryDecision(
                    RecoveryStrategy.REGENERATE_LOCATOR,
                    failure,
                    "re-observe the page and rebuild locator candidates",
                )
            if alternates_available:
                return RecoveryDecision(
                    RecoveryStrategy.ALTERNATE_LOCATOR,
                    failure,
                    "try weaker locator candidates (text, structure)",
                )
            if replan_available:
                return RecoveryDecision(
                    RecoveryStrategy.TARGETED_EXPLORATION,
                    failure,
                    "target no longer findable; explore the local region for a new route",
                )
            if known_state_available:
                return RecoveryDecision(
                    RecoveryStrategy.RETURN_TO_KNOWN_STATE,
                    failure,
                    "return to a known state and re-derive the route",
                )
            return RecoveryDecision(
                RecoveryStrategy.SAFE_STOP, failure, "target is gone and no route remains", terminal=True
            )

        if failure is FailureKind.ELEMENT_DISABLED:
            return RecoveryDecision(
                RecoveryStrategy.TARGETED_EXPLORATION,
                failure,
                "control is disabled: a precondition is unmet; explore for the enabling step"
                + (f" (message: {validation_message})" if validation_message else ""),
            )

        if failure in (FailureKind.VALIDATION_BLOCKED, FailureKind.NO_EFFECT):
            if attempt <= self.max_retries_per_action:
                return RecoveryDecision(
                    RecoveryStrategy.RE_OBSERVE,
                    failure,
                    "no expected effect: re-observe, then retry with fresh evidence",
                    max_attempts=self.max_retries_per_action,
                )
            if replan_available:
                return RecoveryDecision(
                    RecoveryStrategy.TARGETED_EXPLORATION,
                    failure,
                    "repeated no-effect action; search for a different route",
                )
            return RecoveryDecision(
                RecoveryStrategy.SAFE_STOP, failure, "action has no effect and no alternative route", terminal=True
            )

        if failure in (FailureKind.TIMEOUT, FailureKind.STATE_MISMATCH, FailureKind.UNEXPECTED_DIALOG):
            if attempt <= self.max_retries_per_action:
                return RecoveryDecision(
                    RecoveryStrategy.RETRY,
                    failure,
                    "transient state mismatch; re-observe and retry once more",
                    max_attempts=self.max_retries_per_action,
                )
            if known_state_available:
                return RecoveryDecision(
                    RecoveryStrategy.RETURN_TO_KNOWN_STATE,
                    failure,
                    "could not settle; go back to a state the model trusts",
                )
            return RecoveryDecision(RecoveryStrategy.SAFE_STOP, failure, "unrecoverable state mismatch", terminal=True)

        if failure is FailureKind.STALE_WORKFLOW:
            return RecoveryDecision(
                RecoveryStrategy.ALTERNATE_WORKFLOW,
                failure,
                "learned workflow no longer matches the site; try another or explore",
            )

        if failure is FailureKind.LOW_CONFIDENCE or confidence < 0.4:
            return RecoveryDecision(
                RecoveryStrategy.TARGETED_EXPLORATION,
                failure,
                "model confidence too low to act; gather more evidence first",
            )

        if failure is FailureKind.IMPOSSIBLE_TASK:
            return RecoveryDecision(
                RecoveryStrategy.ESCALATE_TO_HUMAN,
                failure,
                "no path exists in the learned model and exploration found no route",
                requires_human=True,
                terminal=True,
            )

        return RecoveryDecision(RecoveryStrategy.RE_OBSERVE, failure, "unknown failure: re-observe before acting")

    def record(self, decision: RecoveryDecision, *, action_id: str | None = None, detail: str = "") -> None:
        self.history.append(
            {
                "failure": decision.failure.value,
                "strategy": decision.strategy.value,
                "reason": decision.reason,
                "action_id": action_id,
                "detail": detail,
            }
        )

    def summary(self) -> dict[str, object]:
        counts: dict[str, int] = {}
        for entry in self.history:
            key = str(entry["failure"])
            counts[key] = counts.get(key, 0) + 1
        return {"recoveries": self.recoveries, "by_failure": counts, "recent": self.history[-20:]}
