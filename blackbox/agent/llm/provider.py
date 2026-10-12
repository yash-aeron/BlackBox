"""The LLM provider abstraction.

Design constraints that shape this module:

* **Optional.** With no provider configured, ``NullProvider`` is used and every
  operation returns ``None``.  The system then runs entirely on deterministic
  observation and interaction - which is how the measurements in the README were
  produced.
* **Structured only.** Every method returns a validated pydantic object from
  ``schemas.py`` (or ``None``).  A malformed response is discarded, never
  partially trusted.
* **Never in the control path.** A proposal goes to the safety policy and the
  executor, which may reject it.  No provider call ever reaches Playwright.
* **Budgeted.** Calls are counted, cached by content hash, and gated by a
  reasoning frequency so the deterministic path is always the default.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from . import prompts
from .schemas import (
    ActionChoice,
    ActionProposal,
    ElementLabeling,
    FailureAnalysis,
    ObservationAnalysis,
    PlanProposal,
    Strict,
    TaskInterpretation,
    WorkflowSummary,
    json_schema,
)

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

FREQUENCY_BUDGETS = {
    "off": 0.0,
    "low": 0.15,
    "medium": 0.4,
    "high": 1.0,
}


@dataclass
class ProviderStats:
    calls: int = 0
    failures: int = 0
    cache_hits: int = 0
    skipped_by_frequency: int = 0
    invalid_responses: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "failures": self.failures,
            "cache_hits": self.cache_hits,
            "skipped_by_frequency": self.skipped_by_frequency,
            "invalid_responses": self.invalid_responses,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "mean_latency_ms": round(self.total_latency_ms / self.calls, 2) if self.calls else 0.0,
        }


class LLMProvider:
    """Interface for every LLM-backed operation in BlackBox."""

    name = "base"

    def __init__(self, *, model: str = "", reasoning_frequency: str = "low", cache: bool = True) -> None:
        self.model = model
        self.frequency = reasoning_frequency if reasoning_frequency in FREQUENCY_BUDGETS else "low"
        self.cache_enabled = cache
        self._cache: dict[str, dict[str, Any]] = {}
        self.stats = ProviderStats()
        self.paused = False

    # -- capability --------------------------------------------------------
    @property
    def available(self) -> bool:
        return False

    @property
    def budget(self) -> float:
        return FREQUENCY_BUDGETS.get(self.frequency, 0.15)

    # -- operations --------------------------------------------------------
    async def analyze_observation(self, *, visible_text: str, elements: str, forms: str = "", dialogs: str = "", alerts: str = "") -> ObservationAnalysis | None:
        return await self._call(
            "analyze_observation",
            prompts.OBSERVATION_ANALYSIS,
            ObservationAnalysis,
            visible_text=visible_text[:6000],
            elements=elements,
            forms=forms,
            dialogs=dialogs,
            alerts=alerts,
        )

    async def semantic_label_elements(self, *, elements: str) -> ElementLabeling | None:
        return await self._call("semantic_label_elements", prompts.SEMANTIC_LABELING, ElementLabeling, elements=elements)

    async def generate_candidate_actions(self, *, summary: str, elements: str, unknown: str = "", tried: str = "") -> ActionProposal | None:
        return await self._call(
            "generate_candidate_actions",
            prompts.CANDIDATE_ACTIONS,
            ActionProposal,
            summary=summary,
            elements=elements,
            unknown=unknown,
            tried=tried,
        )

    async def interpret_task(self, *, text: str) -> TaskInterpretation | None:
        return await self._call("interpret_task", prompts.TASK_INTERPRETATION, TaskInterpretation, text=text[:1000])

    async def generate_plan(self, *, task: str, knowledge: str) -> PlanProposal | None:
        return await self._call(
            "generate_plan",
            "Learned knowledge:\n{knowledge}\n\nTask: {task}\n\nPropose a plan using only known element ids.",
            PlanProposal,
            _schema_name="generate_plan",
            task=task,
            knowledge=knowledge[:6000],
        )

    async def analyze_failure(self, *, action: str, result: str, effects: str = "", alerts: str = "", hypotheses: str = "") -> FailureAnalysis | None:
        return await self._call(
            "analyze_failure",
            prompts.FAILURE_ANALYSIS,
            FailureAnalysis,
            action=action,
            result=result,
            effects=effects,
            alerts=alerts,
            hypotheses=hypotheses,
        )

    async def summarize_workflow(self, *, steps: str, end_state: str = "", parameters: str = "") -> WorkflowSummary | None:
        return await self._call(
            "summarize_workflow",
            prompts.WORKFLOW_SUMMARY,
            WorkflowSummary,
            steps=steps,
            end_state=end_state,
            parameters=parameters,
        )

    async def choose_action(self, *, task: dict[str, Any], page_summary: str, options: list[str]) -> dict[str, Any] | None:
        """Used by the reactive baseline; returns ``{"index": int, "reason": str}``."""
        formatted = "\n".join(f"{index} | {option}" for index, option in enumerate(options))
        choice = await self._call(
            "choose_action",
            prompts.ACTION_CHOICE,
            ActionChoice,
            task=json.dumps(task, default=str)[:800],
            page_summary=page_summary[:2000],
            options=formatted,
        )
        if choice is None:
            return None
        if choice.index >= len(options):
            self.stats.invalid_responses += 1
            return None
        return {"index": choice.index, "reason": choice.reason}

    # -- machinery ---------------------------------------------------------
    async def _call(
        self,
        operation: str,
        template: str,
        schema: type[T],
        *,
        _schema_name: str | None = None,
        **values: Any,
    ) -> T | None:
        if not self.available or self.paused:
            return None
        if self.budget <= 0:
            self.stats.skipped_by_frequency += 1
            return None

        prompt = prompts.format_prompt(template, **values)
        cache_key = hashlib.sha1(f"{operation}|{self.model}|{prompt}".encode()).hexdigest()
        if self.cache_enabled and cache_key in self._cache:
            self.stats.cache_hits += 1
            return schema.model_validate(self._cache[cache_key])

        started = time.perf_counter()
        self.stats.calls += 1
        try:
            raw = await self._complete(prompt, operation, schema)
        except Exception as exc:  # noqa: BLE001 - a provider failure is never fatal
            self.stats.failures += 1
            log.warning("llm provider call failed (%s): %s", operation, exc)
            return None
        finally:
            self.stats.total_latency_ms += (time.perf_counter() - started) * 1000.0

        if raw is None:
            return None
        try:
            parsed = schema.model_validate(raw)
        except ValidationError as exc:
            self.stats.invalid_responses += 1
            log.warning("llm provider returned an invalid %s payload: %s", operation, exc.errors()[:3])
            return None
        if self.cache_enabled:
            self._cache[cache_key] = parsed.model_dump()
        return parsed

    async def _complete(self, prompt: str, operation: str, schema: type[Strict]) -> dict[str, Any] | None:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model,
            "available": self.available,
            "reasoning_frequency": self.frequency,
            "budget": self.budget,
            "paused": self.paused,
            "stats": self.stats.to_dict(),
        }


class NullProvider(LLMProvider):
    """The default: no model, everything deterministic."""

    name = "none"

    @property
    def available(self) -> bool:
        return False

    async def _complete(self, prompt: str, operation: str, schema: type[Strict]) -> dict[str, Any] | None:
        return None


class OpenAICompatibleProvider(LLMProvider):
    """Any OpenAI-compatible chat-completions endpoint (OpenAI, local servers)."""

    name = "openai-compatible"

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None,
        base_url: str | None = None,
        reasoning_frequency: str = "low",
        cache: bool = True,
        timeout: float = 45.0,
    ) -> None:
        super().__init__(model=model, reasoning_frequency=reasoning_frequency, cache=cache)
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self._client: Any | None = None

    @property
    def available(self) -> bool:
        if not self.api_key:
            return False
        try:
            import openai  # noqa: F401
        except ImportError:
            return False
        return True

    def _ensure_client(self) -> Any:
        if self._client is None:
            import openai

            kwargs: dict[str, Any] = {"api_key": self.api_key, "timeout": self.timeout}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = openai.AsyncOpenAI(**kwargs)
        return self._client

    async def _complete(self, prompt: str, operation: str, schema: type[Strict]) -> dict[str, Any] | None:
        client = self._ensure_client()
        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": prompts.SYSTEM_RULES},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_schema", "json_schema": {"name": operation, "schema": json_schema(operation)}},
            temperature=0.0,
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.stats.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            self.stats.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
        content = response.choices[0].message.content or ""
        try:
            return json.loads(content)
        except ValueError:
            self.stats.invalid_responses += 1
            return None


def build_provider(settings: Any) -> LLMProvider:
    """Construct the configured provider, defaulting to the null provider."""
    provider_name = (getattr(settings, "llm_provider", "none") or "none").lower()
    frequency = getattr(settings, "llm_reasoning_frequency", "low")
    cache = bool(getattr(settings, "llm_cache", True))
    if provider_name in ("none", "off", "", "null"):
        return NullProvider(model="", reasoning_frequency=frequency, cache=cache)
    if provider_name in ("openai", "openai-compatible", "compatible"):
        return OpenAICompatibleProvider(
            model=getattr(settings, "llm_model", "") or "gpt-4o-mini",
            api_key=getattr(settings, "llm_api_key", None),
            base_url=getattr(settings, "llm_base_url", None),
            reasoning_frequency=frequency,
            cache=cache,
        )
    log.warning("unknown LLM provider %r; falling back to the deterministic path", provider_name)
    return NullProvider(model="", reasoning_frequency=frequency, cache=cache)


async def gather_or_none(coro: Any) -> Any:
    """Await a provider coroutine, swallowing failures into ``None``."""
    try:
        return await coro
    except Exception:  # noqa: BLE001
        return None


def provider_available(provider: LLMProvider) -> bool:
    return bool(provider and provider.available)


def sleep_briefly() -> None:
    """Placeholder for provider pacing hooks in tests."""
    time.sleep(0)


async def with_timeout(coro: Any, seconds: float) -> Any:
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except asyncio.TimeoutError:
        return None
