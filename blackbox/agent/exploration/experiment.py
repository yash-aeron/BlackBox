"""Experiments, budgets and reproducibility.

An exploration run is an experiment: it has a configuration, a seed, a browser
version, a model version, a prompt version and a budget, and it records the steps
it took.  Two runs with the same deterministic inputs should produce the same
deterministic results, and when they do not, the record shows what differed.
"""

from __future__ import annotations

import platform
import random
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from ..model.base import now_iso, stable_id


@dataclass
class ExplorationBudget:
    max_actions: int = 120
    max_duration_seconds: float = 600.0
    max_states: int = 60
    max_transitions: int = 150
    max_repeated_action: int = 2
    max_same_state_visits: int = 10
    max_browser_restarts: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_actions": self.max_actions,
            "max_duration_seconds": self.max_duration_seconds,
            "max_states": self.max_states,
            "max_transitions": self.max_transitions,
            "max_repeated_action": self.max_repeated_action,
            "max_same_state_visits": self.max_same_state_visits,
            "max_browser_restarts": self.max_browser_restarts,
        }


@dataclass
class BudgetState:
    budget: ExplorationBudget
    actions: int = 0
    states: int = 0
    transitions: int = 0
    browser_restarts: int = 0
    started_at: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def exhausted(self) -> str | None:
        if self.actions >= self.budget.max_actions:
            return f"action budget reached ({self.budget.max_actions})"
        if self.elapsed >= self.budget.max_duration_seconds:
            return f"time budget reached ({self.budget.max_duration_seconds:.0f}s)"
        if self.states >= self.budget.max_states:
            return f"state budget reached ({self.budget.max_states})"
        if self.transitions >= self.budget.max_transitions:
            return f"transition budget reached ({self.budget.max_transitions})"
        if self.browser_restarts > self.budget.max_browser_restarts:
            return f"browser restart budget reached ({self.budget.max_browser_restarts})"
        return None

    def remaining(self) -> dict[str, Any]:
        return {
            "actions": max(0, self.budget.max_actions - self.actions),
            "seconds": max(0.0, round(self.budget.max_duration_seconds - self.elapsed, 1)),
            "states": max(0, self.budget.max_states - self.states),
            "transitions": max(0, self.budget.max_transitions - self.transitions),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "actions_used": self.actions,
            "states_seen": self.states,
            "transitions_seen": self.transitions,
            "elapsed_seconds": round(self.elapsed, 2),
            "remaining": self.remaining(),
            "exhausted": self.exhausted(),
        }


def reproducibility_context(
    *,
    target_id: str,
    seed: int,
    browser_version: str,
    model_version: int,
    prompt_version: str,
    configuration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything needed to re-run and compare an experiment."""
    return {
        "target_id": target_id,
        "seed": seed,
        "browser_version": browser_version,
        "model_version": model_version,
        "prompt_version": prompt_version,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "configuration": configuration or {},
        "recorded_at": now_iso(),
    }


@dataclass
class ExperimentRecord:
    """One exploration or task experiment, with everything needed to audit it."""

    target_id: str
    kind: str = "exploration"
    experiment_id: str = ""
    seed: int = 0
    browser_version: str = ""
    model_version: int = 1
    prompt_version: str = "v1"
    budget: ExplorationBudget = field(default_factory=ExplorationBudget)
    configuration: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=now_iso)
    finished_at: str | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    status: str = "running"

    def __post_init__(self) -> None:
        if not self.experiment_id:
            self.experiment_id = stable_id("exp", self.target_id, self.kind, self.seed, self.started_at)

    def add_step(self, step: dict[str, Any]) -> None:
        step.setdefault("index", len(self.steps))
        self.steps.append(step)

    def finish(self, *, status: str = "completed", metrics: dict[str, Any] | None = None) -> None:
        self.status = status
        self.finished_at = now_iso()
        if metrics:
            self.metrics.update(metrics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "target_id": self.target_id,
            "kind": self.kind,
            "seed": self.seed,
            "browser_version": self.browser_version,
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
            "budget": self.budget.to_dict(),
            "configuration": self.configuration,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "steps": self.steps,
            "metrics": self.metrics,
            "notes": self.notes,
        }


class ExperimentLedger:
    """Keeps experiment records and answers questions about them."""

    def __init__(self) -> None:
        self.records: list[ExperimentRecord] = []

    def add(self, record: ExperimentRecord) -> ExperimentRecord:
        self.records.append(record)
        return record

    def get(self, experiment_id: str) -> ExperimentRecord | None:
        return next((r for r in self.records if r.experiment_id == experiment_id), None)

    def by_kind(self, kind: str) -> list[ExperimentRecord]:
        return [r for r in self.records if r.kind == kind]

    def summary(self) -> dict[str, Any]:
        return {
            "experiments": len(self.records),
            "by_kind": {kind: len(self.by_kind(kind)) for kind in {r.kind for r in self.records}},
            "completed": sum(1 for r in self.records if r.status == "completed"),
            "recent": [r.experiment_id for r in self.records[-5:]],
        }


def deterministic_rng(seed: int) -> random.Random:
    """A seeded RNG: the only randomness the agent is allowed."""
    return random.Random(seed)
