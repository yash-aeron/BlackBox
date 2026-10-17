"""Benchmark runner: measure task execution with and without the learned model.

    python -m blackbox.benchmarks.runner --target demo_crm --mode learned
    python -m blackbox.benchmarks.runner --target demo_crm --mode baseline
    python -m blackbox.benchmarks.runner --target demo_crm --mode compare
    python -m blackbox.benchmarks.runner --learning-curve --target demo_crm
    python -m blackbox.benchmarks.runner --ablations --target demo_crm

Every number in the output comes from a run that actually happened.  Task
definitions live in ``blackbox/benchmarks/tasks/*.json`` and each task starts
from a freshly loaded page so runs are independent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..agent.config import DEMO_APPS, Settings, demo_registration
from ..agent.execution.assertions import Predicate, predicates_from_spec
from ..agent.main import BlackBoxAgent
from ..agent.planning.task_parser import TaskParser
from .baseline import BaselineConfig, ReactiveBaseline
from .metrics.collector import (
    BenchmarkMetrics,
    MetricsCollector,
    TaskMetrics,
    ablation_table,
    compare,
    learning_curve,
)

log = logging.getLogger(__name__)

TASK_DIR = Path(__file__).resolve().parent / "tasks"

MODES = (
    "learned",
    "learned_exploratory",
    "baseline",
    "ablation_states",
    "ablation_transitions",
    "ablation_full",
)


@dataclass
class TaskDefinition:
    task_id: str
    text: str
    target: str
    predicates: list[Predicate] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    difficulty: str = "medium"
    expect_blocked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "text": self.text,
            "target": self.target,
            "tags": self.tags,
            "difficulty": self.difficulty,
            "expect_blocked": self.expect_blocked,
            "predicates": [
                {"kind": p.kind.value, "value": p.value, "target": p.target} for p in self.predicates
            ],
        }


class TaskLibrary:
    """Loads the benchmark task definitions for a target."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or TASK_DIR

    def available_targets(self) -> list[str]:
        return sorted(path.stem for path in self.directory.glob("*.json"))

    def load(self, target: str) -> list[TaskDefinition]:
        path = self.directory / f"{target.replace('demo_', '')}.json"
        if not path.exists():
            path = self.directory / f"{target}.json"
        if not path.exists():
            raise FileNotFoundError(f"no task file for target {target!r} in {self.directory}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        definitions: list[TaskDefinition] = []
        for raw in payload.get("tasks", []):
            definitions.append(
                TaskDefinition(
                    task_id=raw["task_id"],
                    text=raw["text"],
                    target=payload.get("target", target),
                    predicates=predicates_from_spec(raw.get("predicates", [])),
                    tags=raw.get("tags", []),
                    difficulty=raw.get("difficulty", "medium"),
                    expect_blocked=bool(raw.get("expect_blocked", False)),
                )
            )
        return definitions

    def count(self, target: str | None = None) -> int:
        if target:
            return len(self.load(target))
        return sum(len(self.load(name)) for name in self.available_targets())


@dataclass
class BenchmarkReport:
    target: str
    mode: str
    metrics: BenchmarkMetrics
    tasks: list[TaskDefinition] = field(default_factory=list)
    configuration: dict[str, Any] = field(default_factory=dict)
    seed: int = 0
    browser_version: str = ""
    model_version: int = 0
    model_states: int = 0
    model_transitions: int = 0
    model_workflows: int = 0
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "mode": self.mode,
            "seed": self.seed,
            "browser_version": self.browser_version,
            "model_version": self.model_version,
            "model": {
                "states": self.model_states,
                "transitions": self.model_transitions,
                "workflows": self.model_workflows,
            },
            "configuration": self.configuration,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": round(self.duration_seconds, 2),
            "metrics": self.metrics.to_dict(),
            "tasks": [task.to_dict() for task in self.tasks],
        }


class BenchmarkRunner:
    """Runs task suites against a target in one of the comparison modes."""

    def __init__(
        self,
        settings: Settings,
        *,
        target_id: str,
        seed: int | None = None,
        max_actions: int = 30,
        llm: Any | None = None,
    ) -> None:
        self.settings = settings
        self.target_id = target_id
        self.seed = seed if seed is not None else settings.seed
        self.max_actions = max_actions
        self.llm = llm
        self.library = TaskLibrary()

    # -- model-backed modes ------------------------------------------------
    async def run(self, mode: str, *, task_ids: list[str] | None = None) -> BenchmarkReport:
        tasks = self._select_tasks(task_ids)
        started_at = time.time()
        if mode in ("baseline",):
            report = await self._run_baseline(tasks, started_at)
        else:
            report = await self._run_agent(mode, tasks, started_at)
        return report

    def _select_tasks(self, task_ids: list[str] | None) -> list[TaskDefinition]:
        tasks = self.library.load(self.target_id)
        if task_ids:
            wanted = set(task_ids)
            tasks = [task for task in tasks if task.task_id in wanted]
        return tasks

    async def _run_agent(self, mode: str, tasks: list[TaskDefinition], started_at: float) -> BenchmarkReport:
        agent = BlackBoxAgent(self.settings, demo_registration(self.target_id, mode="AUTONOMOUS"))
        await agent.start()
        collector = MetricsCollector(self.target_id, mode)
        use_workflows = mode != "ablation_states"
        use_transitions = mode not in ("ablation_states",)
        allow_exploration = mode in ("learned_exploratory", "ablation_states", "ablation_transitions", "ablation_full")
        try:
            for task in tasks:
                metrics = await self._run_agent_task(
                    agent,
                    task,
                    mode=mode,
                    use_workflows=use_workflows,
                    use_transitions=use_transitions,
                    allow_exploration=allow_exploration,
                )
                collector.add(metrics)
                log.info(
                    "[%s] %s -> %s (%d actions)",
                    mode,
                    task.task_id,
                    "PASS" if metrics.success else ("BLOCKED" if metrics.policy_blocked else "FAIL"),
                    metrics.actions,
                )
            stats = agent.repository.stats()
            report = BenchmarkReport(
                target=self.target_id,
                mode=mode,
                metrics=collector.summarize(tasks_total=len(tasks)),
                tasks=tasks,
                configuration={
                    "use_workflows": use_workflows,
                    "use_transitions": use_transitions,
                    "allow_exploration": allow_exploration,
                    "max_actions": self.max_actions,
                },
                seed=self.seed,
                browser_version=agent.manager.browser_version,
                model_version=agent.repository.model_version,
                model_states=stats["states"],
                model_transitions=stats["transitions"],
                model_workflows=stats["workflows"],
                started_at=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started_at)),
                finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                duration_seconds=time.time() - started_at,
