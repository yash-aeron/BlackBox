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
            )
            return report
        finally:
            await agent.close()

    async def _run_agent_task(
        self,
        agent: BlackBoxAgent,
        task: TaskDefinition,
        *,
        mode: str,
        use_workflows: bool,
        use_transitions: bool,
        allow_exploration: bool,
    ) -> TaskMetrics:
        page = agent.manager.page
        await page.navigate(agent.registration.base_url)
        await page.wait_for_stable(quiet_ms=300, timeout=8)
        result = await agent.run_task(
            task.text,
            predicates=task.predicates,
            allow_exploration=allow_exploration,
            max_exploration_actions=self.max_actions,
            use_workflows=use_workflows,
            use_transitions=use_transitions,
        )
        blocked = self._is_policy_blocked(result)
        metrics = TaskMetrics(
            task_id=task.task_id,
            success=result.success and not blocked,
            policy_blocked=blocked and task.expect_blocked,
            actions=result.actions_executed,
            unnecessary_actions=result.unnecessary_actions,
            duration_seconds=result.duration_seconds,
            planning_latency_ms=result.planning_latency_ms,
            recoveries=result.recoveries,
            replans=result.replans,
            steps_verified=result.steps_verified,
            steps_total=result.steps_total,
            used_workflow=bool(result.used_workflow),
            used_exploration=result.used_exploration,
            plan_source=result.plan_source,
            state_matched=result.current_state_matched,
            predictions_hit=result.transition_predictions_hit,
            predictions_missed=result.transition_predictions_missed,
            verification=result.verification,
            plan=result.plan,
            notes=result.notes[:6],
        )
        if not result.success and not metrics.policy_blocked and task.expect_blocked:
            metrics.notes.append("expected the safety gate to block this task; it did not")
        return metrics

    @staticmethod
    def _is_policy_blocked(result: Any) -> bool:
        haystack = " ".join(
            [str(note) for note in result.notes]
            + [str(step.get("error", "")) for step in result.step_results]
            + [str(step.get("status", "")) for step in result.step_results]
        ).lower()
        return "not approved" in haystack or "blocked" in haystack and "risk" in haystack or "rejected" in haystack

    # -- baseline ----------------------------------------------------------
    async def _run_baseline(self, tasks: list[TaskDefinition], started_at: float) -> BenchmarkReport:
        registration = demo_registration(self.target_id, mode="AUTONOMOUS")
        baseline = ReactiveBaseline(
            self.settings,
            target_id=self.target_id,
            base_url=registration.base_url,
            allowed_origins=registration.normalized_origins(),
            config=BaselineConfig(max_actions=self.max_actions, use_llm=self.llm is not None),
            llm=self.llm,
        )
        await baseline.start()
        collector = MetricsCollector(self.target_id, "baseline")
        parser = TaskParser()
        try:
            for task in tasks:
                spec = parser.parse(task.text)
                outcome = await baseline.run_task(spec, task.predicates)
                collector.add(
                    TaskMetrics(
                        task_id=task.task_id,
                        success=outcome.success,
                        actions=outcome.actions,
                        unnecessary_actions=outcome.unnecessary_actions,
                        duration_seconds=outcome.duration_seconds,
                        planning_latency_ms=outcome.planning_latency_ms,
                        recoveries=outcome.recoveries,
                        plan_source="reactive",
                        notes=outcome.notes[:6],
                    )
                )
                log.info(
                    "[baseline] %s -> %s (%d actions)",
                    task.task_id,
                    "PASS" if outcome.success else "FAIL",
                    outcome.actions,
                )
            return BenchmarkReport(
                target=self.target_id,
                mode="baseline",
                metrics=collector.summarize(tasks_total=len(tasks)),
                tasks=tasks,
                configuration={"max_actions": self.max_actions, "uses_llm": self.llm is not None},
                seed=self.seed,
                browser_version=baseline.manager.browser_version,
                started_at=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started_at)),
                finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                duration_seconds=time.time() - started_at,
            )
        finally:
            await baseline.close()


def save_report(report: BenchmarkReport, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{report.target}-{report.mode}-{int(time.time())}.json"
    path.write_text(json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")
    return path


# -- multi-mode drivers ----------------------------------------------------
async def run_compare(settings: Settings, target: str, *, task_ids: list[str] | None, seed: int) -> dict[str, Any]:
    runner = BenchmarkRunner(settings, target_id=target, seed=seed)
    reports: dict[str, BenchmarkMetrics] = {}
    payloads: dict[str, Any] = {}
    for mode in ("baseline", "learned", "learned_exploratory"):
        report = await runner.run(mode, task_ids=task_ids)
        save_report(report, settings.artifacts_dir / "benchmarks")
        reports[mode] = report.metrics
        payloads[mode] = report.to_dict()
    return {"comparison": compare(reports), "reports": payloads}


async def run_ablations(settings: Settings, target: str, *, task_ids: list[str] | None, seed: int) -> dict[str, Any]:
    runner = BenchmarkRunner(settings, target_id=target, seed=seed)
    results: dict[str, BenchmarkMetrics] = {}
    for label, mode in (
        ("1_no_model_baseline", "baseline"),
        ("2_states_only", "ablation_states"),
        ("3_states_and_transitions", "ablation_transitions"),
        ("4_full_model_with_workflows", "ablation_full"),
    ):
        report = await runner.run(mode, task_ids=task_ids)
        save_report(report, settings.artifacts_dir / "benchmarks")
        results[label] = report.metrics
    return ablation_table(results)


async def run_learning_curve(
    settings: Settings,
    target: str,
    *,
    budgets: list[int],
    probe_task_ids: list[str],
    seed: int,
) -> dict[str, Any]:
    """Explore in increments; after each increment, measure task performance."""
    points: list[dict[str, Any]] = []
    parser = TaskParser()
    for budget in budgets:
        agent = BlackBoxAgent(settings, demo_registration(target, mode="AUTONOMOUS"))
        await agent.start()
        try:
            exploration = await agent.explore(
                agent.default_exploration_config(
                    max_actions=budget,
                    max_seconds=300,
                    configuration_label=f"learning-curve:{budget}",
                )
            )
            agent.mine_workflows()
            stats = agent.repository.stats()
            collector = MetricsCollector(target, f"curve@{budget}")
            tasks = [task for task in TaskLibrary().load(target) if task.task_id in probe_task_ids]
            for task in tasks:
                await agent.manager.page.navigate(agent.registration.base_url)
                await agent.manager.page.wait_for_stable(quiet_ms=250, timeout=6)
                result = await agent.run_task(
                    task.text,
                    predicates=task.predicates,
                    allow_exploration=False,
                )
                collector.add(
                    TaskMetrics(
                        task_id=task.task_id,
                        success=result.success,
                        actions=result.actions_executed,
                        plan_source=result.plan_source,
                        used_workflow=bool(result.used_workflow),
                        used_exploration=result.used_exploration,
                        duration_seconds=result.duration_seconds,
                        planning_latency_ms=result.planning_latency_ms,
                        state_matched=result.current_state_matched,
                    )
                )
            summary = collector.summarize(tasks_total=len(tasks))
            points.append(
                {
                    "actions_spent": exploration.actions_executed,
                    "states_learned": stats["states"],
                    "transitions_learned": stats["transitions"],
                    "verified_transitions": stats["verified_transitions"],
                    "workflows": stats["workflows"],
                    "task_success_rate": summary.success_rate,
                    "mean_actions_per_task": summary.mean_actions,
                    "tasks_run": summary.tasks_run,
                }
            )
            log.info(
                "curve point: %d exploration actions -> %d states, %d workflows, success %.2f",
                exploration.actions_executed,
                stats["states"],
                stats["workflows"],
                summary.success_rate,
            )
        finally:
            await agent.close()
    return learning_curve(points)


async def _main_async(args: argparse.Namespace) -> int:
    settings = Settings()
    settings.ensure_dirs()
    out_dir = Path(args.out) if args.out else settings.artifacts_dir / "benchmarks"
    if args.learning_curve:
        curve = await run_learning_curve(
            settings,
            args.target,
            budgets=args.budgets,
            probe_task_ids=args.probe_tasks,
            seed=args.seed,
        )
        _write(out_dir, f"{args.target}-learning-curve", curve)
        print(json.dumps(curve, indent=2))
        return 0
    if args.ablations:
        table = await run_ablations(settings, args.target, task_ids=args.tasks, seed=args.seed)
        _write(out_dir, f"{args.target}-ablations", table)
        print(json.dumps(table, indent=2))
        return 0
    if args.mode == "compare":
        payload = await run_compare(settings, args.target, task_ids=args.tasks, seed=args.seed)
        _write(out_dir, f"{args.target}-comparison", payload["comparison"])
        print(json.dumps(payload["comparison"], indent=2))
        return 0
    runner = BenchmarkRunner(settings, target_id=args.target, seed=args.seed)
    report = await runner.run(args.mode, task_ids=args.tasks)
    path = save_report(report, out_dir)
    print(json.dumps(report.metrics.to_dict(include_tasks=bool(args.verbose)), indent=2))
    print(f"report written to {path}")
    return 0


def _write(directory: Path, name: str, payload: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BlackBox benchmark runner")
    parser.add_argument("--target", default="demo_crm", choices=sorted(DEMO_APPS))
    parser.add_argument("--mode", default="learned", choices=(*MODES, "compare"))
    parser.add_argument("--tasks", nargs="*", default=None, help="restrict to these task ids")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", default=None)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--ablations", action="store_true")
    parser.add_argument("--learning-curve", action="store_true")
    parser.add_argument("--budgets", nargs="*", type=int, default=[10, 25, 50, 100])
    parser.add_argument(
        "--probe-tasks",
        nargs="*",
        default=["crm_create_customer", "crm_search_customer", "crm_open_add_dialog"],
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
