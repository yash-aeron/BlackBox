"""Benchmark metrics.

Everything reported here is produced by an actual run.  The collector computes
the comparison metrics the research question needs - success rate, actions per
task, wasteful actions, planning latency, recovery count, state recognition and
transition prediction accuracy, workflow reuse - and the learning curve and
ablation tables are derived from real measurements only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskMetrics:
    task_id: str
    mode: str = "learned"
    success: bool = False
    policy_blocked: bool = False
    actions: int = 0
    unnecessary_actions: int = 0
    duration_seconds: float = 0.0
    planning_latency_ms: float = 0.0
    recoveries: int = 0
    replans: int = 0
    steps_verified: int = 0
    steps_total: int = 0
    used_workflow: bool = False
    used_exploration: bool = False
    exploration_actions: int = 0
    plan_source: str = "none"
    state_matched: bool = False
    predictions_hit: int = 0
    predictions_missed: int = 0
    verification: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def model_reuse(self) -> bool:
        """Did the task run from learned knowledge without re-exploring?"""
        return self.plan_source in ("workflow", "graph") and not self.used_exploration

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "mode": self.mode,
            "success": self.success,
            "policy_blocked": self.policy_blocked,
            "actions": self.actions,
            "unnecessary_actions": self.unnecessary_actions,
            "duration_seconds": round(self.duration_seconds, 2),
            "planning_latency_ms": round(self.planning_latency_ms, 2),
            "recoveries": self.recoveries,
            "replans": self.replans,
            "steps_verified": self.steps_verified,
            "steps_total": self.steps_total,
            "used_workflow": self.used_workflow,
            "used_exploration": self.used_exploration,
            "exploration_actions": self.exploration_actions,
            "plan_source": self.plan_source,
            "state_matched": self.state_matched,
            "predictions_hit": self.predictions_hit,
            "predictions_missed": self.predictions_missed,
            "model_reuse": self.model_reuse,
            "verification": self.verification,
            "plan": self.plan,
            "notes": self.notes,
        }


@dataclass
class BenchmarkMetrics:
    target: str
    mode: str
    tasks_total: int = 0
    tasks_run: int = 0
    successes: int = 0
    policy_blocked: int = 0
    success_rate: float = 0.0
    mean_actions: float = 0.0
    mean_unnecessary_actions: float = 0.0
    mean_duration_seconds: float = 0.0
    mean_planning_latency_ms: float = 0.0
    total_recoveries: int = 0
    total_replans: int = 0
    model_reuse_rate: float = 0.0
    workflow_reuse_rate: float = 0.0
    workflow_reuse_success_rate: float = 0.0
    state_recognition_accuracy: float = 0.0
    transition_prediction_accuracy: float = 0.0
    mean_step_verification_rate: float = 0.0
    exploration_actions: int = 0
    per_task: list[TaskMetrics] = field(default_factory=list)

    def to_dict(self, *, include_tasks: bool = True) -> dict[str, Any]:
        payload = {
            "target": self.target,
            "mode": self.mode,
            "tasks_total": self.tasks_total,
            "tasks_run": self.tasks_run,
            "successes": self.successes,
            "policy_blocked": self.policy_blocked,
            "success_rate": round(self.success_rate, 4),
            "mean_actions": round(self.mean_actions, 3),
            "mean_unnecessary_actions": round(self.mean_unnecessary_actions, 3),
            "mean_duration_seconds": round(self.mean_duration_seconds, 2),
            "mean_planning_latency_ms": round(self.mean_planning_latency_ms, 2),
            "total_recoveries": self.total_recoveries,
            "total_replans": self.total_replans,
            "model_reuse_rate": round(self.model_reuse_rate, 4),
            "workflow_reuse_rate": round(self.workflow_reuse_rate, 4),
            "workflow_reuse_success_rate": round(self.workflow_reuse_success_rate, 4),
            "state_recognition_accuracy": round(self.state_recognition_accuracy, 4),
            "transition_prediction_accuracy": round(self.transition_prediction_accuracy, 4),
            "mean_step_verification_rate": round(self.mean_step_verification_rate, 4),
            "exploration_actions": self.exploration_actions,
        }
        if include_tasks:
            payload["per_task"] = [task.to_dict() for task in self.per_task]
        return payload


class MetricsCollector:
    """Accumulates task results and computes the comparison metrics."""

    def __init__(self, target: str, mode: str) -> None:
        self.target = target
        self.mode = mode
        self.tasks: list[TaskMetrics] = []

    def add(self, metrics: TaskMetrics) -> None:
        metrics.mode = self.mode
        self.tasks.append(metrics)

    # -- derived -----------------------------------------------------------
    def summarize(self, *, tasks_total: int | None = None) -> BenchmarkMetrics:
        tasks = [task for task in self.tasks if not task.policy_blocked]
        blocked = [task for task in self.tasks if task.policy_blocked]
        summary = BenchmarkMetrics(
            target=self.target,
            mode=self.mode,
            tasks_total=tasks_total if tasks_total is not None else len(self.tasks),
            tasks_run=len(self.tasks),
            successes=sum(1 for task in tasks if task.success),
            policy_blocked=len(blocked),
            per_task=list(self.tasks),
        )
        summary.success_rate = (summary.successes / len(tasks)) if tasks else 0.0
        summary.mean_actions = _mean([task.actions for task in tasks])
        summary.mean_unnecessary_actions = _mean([task.unnecessary_actions for task in tasks])
        summary.mean_duration_seconds = _mean([task.duration_seconds for task in tasks])
        summary.mean_planning_latency_ms = _mean([task.planning_latency_ms for task in tasks])
        summary.total_recoveries = sum(task.recoveries for task in tasks)
        summary.total_replans = sum(task.replans for task in tasks)
        summary.exploration_actions = sum(task.exploration_actions for task in tasks)
        summary.model_reuse_rate = _rate([task.model_reuse for task in tasks])
        summary.workflow_reuse_rate = _rate([task.used_workflow for task in tasks])
        workflow_tasks = [task for task in tasks if task.used_workflow]
        summary.workflow_reuse_success_rate = _rate([task.success for task in workflow_tasks])
        summary.state_recognition_accuracy = _rate([task.state_matched for task in tasks])
        hits = sum(task.predictions_hit for task in tasks)
        misses = sum(task.predictions_missed for task in tasks)
        summary.transition_prediction_accuracy = (hits / (hits + misses)) if (hits + misses) else 0.0
        rates = [
            (task.steps_verified / task.steps_total)
            for task in tasks
            if task.steps_total
        ]
        summary.mean_step_verification_rate = _mean(rates)
        return summary


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _rate(flags: list[bool]) -> float:
    return (sum(1 for flag in flags if flag) / len(flags)) if flags else 0.0


def compare(reports: dict[str, BenchmarkMetrics]) -> dict[str, Any]:
    """A baseline-vs-learned comparison table built from real reports."""
    rows: list[dict[str, Any]] = []
    for mode, metrics in sorted(reports.items()):
        rows.append(
            {
                "mode": mode,
                "success_rate": round(metrics.success_rate, 4),
                "mean_actions": round(metrics.mean_actions, 3),
                "mean_unnecessary_actions": round(metrics.mean_unnecessary_actions, 3),
                "mean_duration_seconds": round(metrics.mean_duration_seconds, 2),
                "mean_planning_latency_ms": round(metrics.mean_planning_latency_ms, 2),
                "model_reuse_rate": round(metrics.model_reuse_rate, 4),
                "workflow_reuse_rate": round(metrics.workflow_reuse_rate, 4),
                "recoveries": metrics.total_recoveries,
                "tasks_run": metrics.tasks_run,
                "policy_blocked": metrics.policy_blocked,
            }
        )
    baseline = reports.get("baseline")
    learned = reports.get("learned")
    delta: dict[str, Any] = {}
    if baseline and learned:
        delta = {
            "success_rate_delta": round(learned.success_rate - baseline.success_rate, 4),
            "actions_delta": round(learned.mean_actions - baseline.mean_actions, 3),
            "actions_reduction_pct": (
                round((baseline.mean_actions - learned.mean_actions) / baseline.mean_actions * 100, 2)
                if baseline.mean_actions
                else None
            ),
            "planning_latency_delta_ms": round(
                learned.mean_planning_latency_ms - baseline.mean_planning_latency_ms, 2
            ),
        }
    return {"rows": rows, "learned_vs_baseline": delta}


def learning_curve(points: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn measured (budget -> outcome) samples into a curve description."""
    ordered = sorted(points, key=lambda point: point.get("actions_spent", 0))
    return {
        "points": [
            {
                "actions_spent": point.get("actions_spent", 0),
                "states_learned": point.get("states_learned", 0),
                "transitions_learned": point.get("transitions_learned", 0),
                "verified_transitions": point.get("verified_transitions", 0),
                "workflows": point.get("workflows", 0),
                "task_success_rate": round(point.get("task_success_rate", 0.0), 4),
                "mean_actions_per_task": round(point.get("mean_actions_per_task", 0.0), 3),
                "tasks_run": point.get("tasks_run", 0),
            }
            for point in ordered
        ],
        "measured": True,
    }


def ablation_table(results: dict[str, BenchmarkMetrics]) -> dict[str, Any]:
    """Which model component contributes what, from measured runs."""
    rows: list[dict[str, Any]] = []
    for label, metrics in sorted(results.items()):
        rows.append(
            {
                "configuration": label,
                "success_rate": round(metrics.success_rate, 4),
                "mean_actions": round(metrics.mean_actions, 3),
                "mean_planning_latency_ms": round(metrics.mean_planning_latency_ms, 2),
                "exploration_actions": metrics.exploration_actions,
                "model_reuse_rate": round(metrics.model_reuse_rate, 4),
                "tasks_run": metrics.tasks_run,
            }
        )
    return {"rows": rows, "measured": True}
