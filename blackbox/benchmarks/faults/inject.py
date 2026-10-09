"""Failure-injection experiments.

Each fault is injected by the proxy into the responses the browser receives, then
a task that previously succeeded is run against the patched site.  Three
properties are checked, and they are the properties that matter for a system
whose knowledge is supposed to be earned:

1. **No false success** - the agent never reports success unless its declared
   success conditions were actually satisfied.
2. **No unfounded verification** - nothing is marked VERIFIED without evidence,
   and a transition is never VERIFIED with a zero success rate.
3. **Safe termination** - a fault either produces a real recovery, or the run
   stops with a reason; the process does not crash, and the origin boundary
   holds even when the site tries to send the browser elsewhere.

Usage:
    python -m blackbox.benchmarks.faults.inject --target demo_crm --task crm_create_customer
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

from ...agent.config import Settings, demo_registration
from ...agent.execution.assertions import Predicate, predicates_from_spec
from ...agent.main import BlackBoxAgent
from ..runner import TaskLibrary
from .proxy import FaultSpec, start_proxy

log = logging.getLogger(__name__)

# Faults are expressed in terms of the demo markup (ids, labels, attributes); the
# agent side knows nothing about them.  Every marker below was verified to exist
# in the served files, and matching is content-driven rather than path-driven:
# the demo apps are served at "/" and "/app.js", so a path filter on
# "index.html" would silently never fire.
FAULT_LIBRARY: dict[str, list[FaultSpec]] = {
    "renamed_label": [
        FaultSpec(kind="rename_label", args={"old": "Add customer", "new": "New record"}),
    ],
    "renamed_field": [
        FaultSpec(kind="rename_field", args={"old": 'name="name"', "new": 'name="full_name"'}),
    ],
    "removed_control": [
        FaultSpec(kind="remove_element", args={"marker": 'id="customer-name"'}),
    ],
    "duplicated_control": [
        FaultSpec(kind="duplicate_element", args={"marker": 'id="customer-save"'}),
    ],
    "delayed_render": [
        FaultSpec(kind="delay", args={"ms": 1800}),
    ],
    "unexpected_dialog": [
        FaultSpec(kind="inject_dialog", args={"message": "Your session has expired."}),
    ],
    "broken_navigation": [
        FaultSpec(
            kind="break_navigation",
            args={"target": "#/dashboard", "replacement": "http://127.0.0.1:3999/elsewhere"},
        ),
    ],
    "broken_script": [
        FaultSpec(kind="break_script", args={"marker": "addEventListener"}, path_contains="app.js"),
    ],
    "server_error": [
        FaultSpec(
            kind="fail_request",
            args={"status": "500", "body": "injected server error"},
            path_contains="app.js",
        ),
    ],
}


@dataclass
class FaultOutcome:
    fault: str
    target: str
    task_id: str
    proxy_applied: int = 0
    task_success: bool = False
    no_false_success: bool = True
    no_unfounded_verification: bool = True
    safe_termination: bool = True
    origin_boundary_held: bool = True
    crashed: bool = False
    harness_error: bool = False
    actions: int = 0
    duration_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)
    stop_reason: str = ""

    @property
    def passed(self) -> bool:
        """A fault is handled correctly when it actually fired, nothing was
        overclaimed, and the run ended safely."""
        return (
            not self.harness_error
            and not self.crashed
            and self.no_false_success
            and self.no_unfounded_verification
            and self.safe_termination
            and self.origin_boundary_held
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fault": self.fault,
            "target": self.target,
            "task_id": self.task_id,
            "proxy_applied": self.proxy_applied,
            "harness_error": self.harness_error,
            "task_success": self.task_success,
            "handled_correctly": self.passed,
            "no_false_success": self.no_false_success,
            "no_unfounded_verification": self.no_unfounded_verification,
            "safe_termination": self.safe_termination,
            "origin_boundary_held": self.origin_boundary_held,
            "crashed": self.crashed,
            "actions": self.actions,
            "duration_seconds": round(self.duration_seconds, 2),
            "stop_reason": self.stop_reason,
            "notes": self.notes,
        }


class FailureInjectionSuite:
    """Runs the fault library against one target and one task."""

    def __init__(self, settings: Settings, *, target: str = "demo_crm", task_id: str = "crm_create_customer") -> None:
        self.settings = settings
        self.target = target
        self.task_id = task_id
        self.library = TaskLibrary()

    def _task(self):
        tasks = self.library.load(self.target)
        task = next((item for item in tasks if item.task_id == self.task_id), None)
        if task is None:
            raise KeyError(f"task {self.task_id!r} not found for {self.target}")
        return task

    async def run_fault(self, fault_name: str, *, port: int, allow_exploration: bool = True) -> FaultOutcome:
        faults = FAULT_LIBRARY[fault_name]
        registration = demo_registration(self.target, mode="AUTONOMOUS")
        upstream = registration.base_url
        server, injector = start_proxy(upstream, port, faults)
        proxied = f"http://127.0.0.1:{port}"
        task = self._task()
        outcome = FaultOutcome(fault=fault_name, target=self.target, task_id=self.task_id)

        agent = BlackBoxAgent(
            self.settings,
            demo_registration(self.target, mode="AUTONOMOUS", base_url=proxied),
        )
        started = time.monotonic()
        try:
            await agent.start()
            # The task must start from the proxied site, otherwise the browser
            # never talks to the proxy and no fault is ever injected.
            page = agent.manager.page
            await page.navigate(proxied)
            await page.wait_for_stable(quiet_ms=300, timeout=8)
            result = await agent.run_task(
                task.text,
                predicates=task.predicates,
                allow_exploration=allow_exploration,
                max_exploration_actions=12,
            )
            outcome.actions = result.actions_executed
            outcome.task_success = bool(result.success)
            outcome.notes.extend(result.notes[:6])
            outcome.stop_reason = (result.plan or {}).get("source", "none")

            # 1. No false success: declared conditions must actually hold.
            declared = result.verification.get("predicates", []) if isinstance(result.verification, dict) else []
            if result.success and declared and not all(item.get("satisfied") for item in declared):
                outcome.no_false_success = False
                outcome.notes.append("reported success while a declared condition was unsatisfied")

            # 2. No unfounded verification.
            for transition in agent.graph.transitions.values():
                if transition.status.value == "VERIFIED" and transition.execution_count > 0 and transition.success_count == 0:
                    outcome.no_unfounded_verification = False
                    outcome.notes.append(f"transition {transition.transition_id} verified with zero successes")
                if transition.status.value == "VERIFIED" and not transition.evidence_ids:
                    outcome.no_unfounded_verification = False
                    outcome.notes.append(f"transition {transition.transition_id} verified without evidence")

            # 3. Safe termination: we are here, the browser is usable, and the
            #    boundary held even if the site tried to send us elsewhere.
            outcome.safe_termination = agent.manager.alive()
            if not outcome.safe_termination:
                outcome.notes.append("browser did not survive the injected fault")
            blocked = agent.sandbox.snapshot().get("blocked_count", 0)
            if fault_name == "broken_navigation":
                # The property that matters is that the browser is still inside
                # the authorized origins.  Whether the sandbox had to intervene
                # depends on how the page attempted the move: browsers reject a
                # cross-origin history.replaceState outright, in which case there
                # is nothing for the sandbox to block.
                final_url = await agent.manager.page.url() if agent.manager.alive() else ""
                inside = (not final_url) or agent.sandbox.is_allowed(final_url).allowed
                outcome.origin_boundary_held = bool(inside)
                if inside and not blocked:
                    outcome.notes.append(
                        "browser stayed inside the authorized origin; the foreign target was rejected before navigation"
                    )
        except Exception as exc:  # noqa: BLE001 - a crash is a result, not an error to hide
            outcome.crashed = True
            outcome.safe_termination = False
            outcome.notes.append(f"exception during fault run: {exc}")
        finally:
            outcome.proxy_applied = len(injector.applied)
            outcome.duration_seconds = time.monotonic() - started
            try:
                await agent.close()
            except Exception:  # noqa: BLE001
                pass
            server.shutdown()
        if outcome.proxy_applied == 0:
            outcome.harness_error = True
            outcome.notes.append("fault was never applied (the response did not contain the marker)")
        return outcome

    async def run_all(self, *, start_port: int = 3011, faults: list[str] | None = None) -> dict[str, Any]:
        results: list[FaultOutcome] = []
        names = faults or list(FAULT_LIBRARY)
        for index, name in enumerate(names):
            outcome = await self.run_fault(name, port=start_port + index)
            results.append(outcome)
            log.info(
                "[fault] %-20s applied=%d success=%s handled=%s",
                name,
                outcome.proxy_applied,
                outcome.task_success,
                outcome.passed,
            )
        injected = [outcome for outcome in results if not outcome.harness_error]
        passed = sum(1 for outcome in injected if outcome.passed)
        return {
            "target": self.target,
            "task_id": self.task_id,
            "faults_run": len(results),
            "faults_injected": len(injected),
            "harness_errors": len(results) - len(injected),
            "handled_correctly": passed,
            "handled_rate": round(passed / len(injected), 4) if injected else 0.0,
            "no_false_success": all(outcome.no_false_success for outcome in injected),
            "no_unfounded_verification": all(outcome.no_unfounded_verification for outcome in injected),
            "safe_termination": all(outcome.safe_termination for outcome in injected),
            "recovered": sum(1 for outcome in injected if outcome.task_success),
            "results": [outcome.to_dict() for outcome in results],
            "measured": True,
        }


async def _main_async(args: argparse.Namespace) -> int:
    settings = Settings()
    settings.ensure_dirs()
    suite = FailureInjectionSuite(settings, target=args.target, task_id=args.task)
    report = await suite.run_all(start_port=args.port, faults=args.faults)
    out_dir = settings.artifacts_dir / "benchmarks"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{args.target}-failure-injection.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
    for result in report["results"]:
        print(
            f"  {result['fault']:22s} applied={result['proxy_applied']} "
            f"success={str(result['task_success']):5s} handled={result['handled_correctly']} "
            f"notes={'; '.join(result['notes'][:2])[:100]}"
        )
    print(f"report written to {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BlackBox failure-injection suite")
    parser.add_argument("--target", default="demo_crm")
    parser.add_argument("--task", default="crm_create_customer")
    parser.add_argument("--port", type=int, default=3011)
    parser.add_argument("--faults", nargs="*", default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
