"""The baseline: observe the page, decide the next action, execute, repeat.

This is the comparison BlackBox must beat.  It uses the *same* deterministic
primitives - the same element detector, action generator, safety classifier and
executor - so the only difference is that it has no persistent model: no learned
transitions, no workflows, no prerequisite memory.  Every task starts from
nothing and reasons from the current page alone.

When an LLM provider is configured, the baseline asks it which candidate action
to take (the classic "LLM decides the next click" loop); otherwise it uses a
deterministic heuristic over the same candidates.  Both variants are reported,
because the research claim is about the learned model, not about the model's
replacement.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from ..agent.config import Settings
from ..agent.execution.assertions import Predicate, AssertionEngine
from ..agent.execution.executor import ExecutionConfig, Executor
from ..agent.execution.locator import LocatorResolver
from ..agent.model.action import Action, ActionType
from ..agent.model.base import normalize_text
from ..agent.observation.browser import PageObserver
from ..agent.observation.network_guard import NetworkGuard
from ..agent.perception.element_detector import ElementDetector
from ..agent.perception.semantic_labeler import SemanticIntent, SemanticLabeler
from ..agent.planning.task_parser import TaskParser, TaskSpec
from ..agent.exploration.action_generator import ActionGenerator
from ..agent.exploration.safety import RiskLevel, SafetyClassifier, SafetyPolicy

from blackbox.browser.manager import BrowserManager
from blackbox.browser.permissions import default_policy
from blackbox.browser.sandbox import Sandbox

# A long benchmark run will eventually lose a browser; the baseline recovers the
# same way the agent does, so a crash does not silently become a task failure.
BrowserFailure = (aiohttp.ClientConnectionResetError, aiohttp.ClientError, OSError, RuntimeError)


@dataclass
class BaselineConfig:
    max_actions: int = 25
    allow_risk: RiskLevel = RiskLevel.MEDIUM
    use_llm: bool = False


@dataclass
class BaselineResult:
    task_id: str
    success: bool
    actions: int = 0
    unnecessary_actions: int = 0
    duration_seconds: float = 0.0
    planning_latency_ms: float = 0.0
    recoveries: int = 0
    notes: list[str] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "success": self.success,
            "actions": self.actions,
            "unnecessary_actions": self.unnecessary_actions,
            "duration_seconds": round(self.duration_seconds, 2),
            "planning_latency_ms": round(self.planning_latency_ms, 2),
            "recoveries": self.recoveries,
            "notes": self.notes,
            "trace": self.trace[-40:],
        }


class ReactiveBaseline:
    """No model, no memory: reason from the current page on every step."""

    def __init__(
        self,
        settings: Settings,
        *,
        target_id: str,
        base_url: str,
        allowed_origins: list[str],
        config: BaselineConfig | None = None,
        llm: Any | None = None,
    ) -> None:
        self.settings = settings
        self.target_id = target_id
        self.base_url = base_url
        self.config = config or BaselineConfig()
        self.llm = llm
        self.sandbox = Sandbox(allowed_origins=allowed_origins)
        self.network_guard = NetworkGuard(allowed_origins=allowed_origins)
        self.manager = BrowserManager(
            sandbox=self.sandbox,
            permissions=default_policy(settings.artifacts_dir / "downloads"),
            artifacts_dir=settings.artifacts_dir / "baseline",
            headless=settings.headless,
            window_size=(settings.viewport_width, settings.viewport_height),
        )
        self.observer = PageObserver(settings.artifacts_dir / "baseline", capture_screenshots=False)
        self.detector = ElementDetector()
        self.generator = ActionGenerator()
        self.safety = SafetyClassifier()
        self.policy = SafetyPolicy(mode="AUTONOMOUS", allow_medium_with_config=True)  # type: ignore[arg-type]
        self.executor = Executor(
            resolver=LocatorResolver(network_guard=self.network_guard),
            config=ExecutionConfig(upload_fixtures=settings.artifacts_dir / "fixtures"),
        )
        self.assertions = AssertionEngine()
        self.labeler = SemanticLabeler()
        self.parser = TaskParser()

    async def start(self) -> None:
        await self.manager.start()

    async def close(self) -> None:
        await self.manager.close()

    async def _live_page(self, result: BaselineResult):
        """Open the target page, starting or rebuilding the browser as needed."""
        try:
            if not self.manager.alive():
                await self.manager.start()
            page = self.manager.page
            await page.navigate(self.base_url)
            await page.wait_for_stable(quiet_ms=300, timeout=8)
            return page
        except Exception as exc:  # noqa: BLE001 - reported as a result, not raised
            result.notes.append(f"could not open {self.base_url}: {exc}")
            return None

    async def _recover(self, page, result: BaselineResult):
        """Rebuild the browser after a crash and reopen the target page."""
        try:
            page = await self.manager.restart()
            await page.navigate(self.base_url)
            await page.wait_for_stable(quiet_ms=300, timeout=8)
            result.notes.append("browser restarted")
            return page
        except Exception as exc:  # noqa: BLE001
            result.notes.append(f"browser restart failed: {exc}")
            return None

    async def run_task(self, task: TaskSpec, predicates: list[Predicate]) -> BaselineResult:
        started = time.monotonic()
        result = BaselineResult(task_id=task.task_id, success=False)
        page = await self._live_page(result)
        if page is None:
            result.duration_seconds = time.monotonic() - started
            return result

        for step in range(self.config.max_actions):
            try:
                observation = await self.observer.observe(page)
            except BrowserFailure as exc:  # noqa: BLE001 - a dead browser is recoverable
                result.recoveries += 1
                result.notes.append(f"browser failure at step {step}: {exc}")
                page = await self._recover(page, result)
                if page is None:
                    break
                continue
            elements = self.detector.detect(observation)
            observation.interactive_elements = elements

            satisfied, predicate_results = self.assertions.evaluate_all(predicates, observation, None)
            if satisfied:
                result.success = True
                result.notes.append(f"success condition met after {step} action(s)")
                break

            decision_started = time.perf_counter()
            action, element, reason = await self._decide(task, observation, elements)
            result.planning_latency_ms += (time.perf_counter() - decision_started) * 1000.0
            if action is None:
                result.notes.append("no candidate action remains")
                break

            execution = await self.executor.execute(page, action, element=element)
            result.actions += 1
            try:
                await page.wait_for_stable(quiet_ms=250, timeout=6)
                after = await self.observer.observe(page)
            except BrowserFailure as exc:  # noqa: BLE001 - the action may have killed it
                result.recoveries += 1
                result.notes.append(f"browser failure after step {step}: {exc}")
                page = await self._recover(page, result)
                if page is None:
                    break
                continue
            after.interactive_elements = self.detector.detect(after)
            changed = normalize_text(after.visible_text) != normalize_text(observation.visible_text) or bool(
                after.dialogs
            ) != bool(observation.dialogs)
            if not changed and execution.ok:
                result.unnecessary_actions += 1
            result.trace.append(
                {
                    "step": step,
                    "action": action.describe(),
                    "reason": reason,
                    "status": execution.status,
                    "effected": changed,
                }
            )
            if not execution.ok:
                result.recoveries += 1
        else:
            result.notes.append(f"action budget exhausted ({self.config.max_actions})")

        if not result.success:
            observation = await self.observer.observe(page)
            observation.interactive_elements = self.detector.detect(observation)
            satisfied, _ = self.assertions.evaluate_all(predicates, observation, None)
            result.success = satisfied
        result.duration_seconds = time.monotonic() - started
        return result

    # -- decision ----------------------------------------------------------
    async def _decide(self, task: TaskSpec, observation: Any, elements: list[Any]) -> tuple[Action | None, Any, str]:
        candidates = self.generator.generate(observation, elements=elements)
        if not candidates:
            return None, None, "no candidates"
        scored: list[tuple[float, Action, Any, str]] = []
        for action in candidates:
            element = next(
                (e for e in elements if action.target and e.element_id == action.target.element_id), None
            )
            risk = self.safety.classify(action, element=element, observation=observation)
            if risk.risk.rank > self.config.allow_risk.rank:
                continue
            keyword_score, reason = self._keyword_score(task, action, element)
            scored.append((keyword_score, action, element, reason))
        if not scored:
            return None, None, "every candidate exceeded the risk threshold"
        scored.sort(key=lambda item: -item[0])

        if self.config.use_llm and self.llm is not None:
            proposal = await self._llm_choice(task, observation, scored)
            if proposal is not None:
                return proposal[1], proposal[2], "chosen by the language model"
        best = scored[0]
        return best[1], best[2], best[3]

    def _keyword_score(self, task: TaskSpec, action: Action, element: Any) -> tuple[float, str]:
        """A flat heuristic over the current page: no learned knowledge at all."""
        label = normalize_text(action.target.label() if action.target else "")
        goal_words = {word for word in normalize_text(task.raw).split() if len(word) > 2}
        entity_values = {normalize_text(value) for value in task.entities.values()}
        score = 0.0
        reasons: list[str] = []

        overlap = len(goal_words & set(label.split()))
        if overlap:
            score += 0.6 * overlap
            reasons.append(f"label overlaps the task wording ({overlap})")
        if any(value and value in label for value in entity_values):
            score += 0.5
            reasons.append("label mentions a task value")
        if element is not None:
            intent = self.labeler.label_element(element).intent
            if intent in (SemanticIntent.CREATE, SemanticIntent.SUBMIT) and task.verb in ("create", "submit"):
                score += 0.4
                reasons.append(f"intent {intent.value} matches the goal verb")
            if intent in (SemanticIntent.SEARCH, SemanticIntent.FILTER) and task.verb in ("search", "filter"):
                score += 0.4
                reasons.append(f"intent {intent.value} matches the goal verb")
            if intent is SemanticIntent.EXPORT and task.verb == "export":
                score += 0.5
                reasons.append("export control matches the goal")
            if action.type is ActionType.TYPE and task.entities:
                for key in task.entities:
                    if key in label:
                        score += 0.7
                        reasons.append(f"field matches entity {key!r}")
                        break
        if not reasons:
            reasons.append("no strong signal; exploring")
        return score, "; ".join(reasons)

    async def _llm_choice(self, task: TaskSpec, observation: Any, scored: list[tuple[float, Action, Any, str]]):
        """Ask the provider to pick among *pre-validated* candidates only."""
        if self.llm is None:
            return None
        try:
            options = [action.describe() for _, action, _, _ in scored[:12]]
            choice = await self.llm.choose_action(
                task=task.to_dict(),
                page_summary=self.labeler.observation_summary(observation),
                options=options,
            )
        except Exception:  # noqa: BLE001 - a provider failure must not break the baseline
            return None
        if not choice:
            return None
        index = choice.get("index")
        if isinstance(index, int) and 0 <= index < len(scored):
            _, action, element, _ = scored[index]
            return action, element
        return None

