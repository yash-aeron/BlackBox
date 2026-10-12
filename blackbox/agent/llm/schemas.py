"""Structured output schemas for every LLM operation.

The model never emits a browser command.  Each operation returns a validated
object from this module, which the deterministic code then treats as a
*proposal*: it is checked against safety policy, origin restrictions and the
observation it claims to describe before anything happens.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ObservationAnalysis(Strict):
    """Semantic reading of an observed page."""

    page_kind: str = Field(description="list | table | form | dialog | wizard | dashboard | settings | detail | login | unknown")
    summary: str = Field(max_length=400)
    notable_controls: list[str] = Field(default_factory=list, max_length=20)
    possible_goals: list[str] = Field(default_factory=list, max_length=8)
    uncertainties: list[str] = Field(default_factory=list, max_length=8)


class ElementLabel(Strict):
    element_id: str
    role: str = ""
    meaning: str = Field(max_length=120, description="what this control means to a user")
    intent: Literal[
        "CREATE",
        "READ",
        "UPDATE",
        "DELETE",
        "SEARCH",
        "FILTER",
        "SORT",
        "NAVIGATE",
        "SUBMIT",
        "CANCEL",
        "CONFIRM",
        "EXPORT",
        "DOWNLOAD",
        "UPLOAD",
        "PAY",
        "SEND",
        "PUBLISH",
        "AUTHENTICATE",
        "PAGINATE",
        "TOGGLE",
        "OPEN",
        "CLOSE",
        "UNKNOWN",
    ] = "UNKNOWN"
    risk_hint: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "LOW"


class ElementLabeling(Strict):
    labels: list[ElementLabel] = Field(default_factory=list, max_length=80)


class CandidateAction(Strict):
    """A proposed action, expressed only in terms the executor already supports."""

    action_type: Literal[
        "CLICK",
        "TYPE",
        "CLEAR",
        "SELECT",
        "CHECK",
        "UNCHECK",
        "PRESS_KEY",
        "HOTKEY",
        "SCROLL",
        "NAVIGATE_BACK",
        "UPLOAD",
    ]
    target_element_id: str = ""
    value: str | None = None
    key: str | None = None
    rationale: str = Field(max_length=240)
    expected_effect: list[str] = Field(default_factory=list, max_length=6)


class ActionProposal(Strict):
    actions: list[CandidateAction] = Field(default_factory=list, max_length=12)


class TaskInterpretation(Strict):
    goal: str = Field(max_length=80)
    entity_noun: str = Field(default="", max_length=40)
    entities: dict[str, str] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list, max_length=6)
    expected_end_state: str = Field(default="", max_length=120)


class PlanStepProposal(Strict):
    action_type: str
    target_element_id: str = ""
    value: str | None = None
    description: str = Field(default="", max_length=200)


class PlanProposal(Strict):
    steps: list[PlanStepProposal] = Field(default_factory=list, max_length=20)
    reasoning: str = Field(default="", max_length=600)
    confidence: float = Field(default=0.3, ge=0.0, le=1.0)


class FailureAnalysis(Strict):
    failure_kind: Literal[
        "LOCATOR_FAILURE",
        "ELEMENT_DISABLED",
        "NO_EFFECT",
        "UNEXPECTED_DIALOG",
        "NAVIGATION_FAILURE",
        "TIMEOUT",
        "STATE_MISMATCH",
        "VALIDATION_BLOCKED",
        "IMPOSSIBLE_TASK",
        "UNKNOWN",
    ] = "UNKNOWN"
    explanation: str = Field(max_length=400)
    suggested_conditions: list[str] = Field(default_factory=list, max_length=6)
    confidence: float = Field(default=0.3, ge=0.0, le=1.0)


class WorkflowSummary(Strict):
    name: str = Field(max_length=60)
    goal: str = Field(max_length=60)
    description: str = Field(max_length=300)
    parameters: list[str] = Field(default_factory=list, max_length=12)


class ActionChoice(Strict):
    """The baseline's classic "which of these should I do next?" decision."""

    index: int = Field(ge=0)
    reason: str = Field(default="", max_length=240)


class StructuredPayloads:
    """Names of the operations the provider exposes, for logging and caching."""

    OPERATIONS = (
        "analyze_observation",
        "semantic_label_elements",
        "generate_candidate_actions",
        "interpret_task",
        "generate_plan",
        "analyze_failure",
        "summarize_workflow",
        "choose_action",
    )


def schema_for(operation: str) -> type[Strict]:
    mapping: dict[str, type[Strict]] = {
        "analyze_observation": ObservationAnalysis,
        "semantic_label_elements": ElementLabeling,
        "generate_candidate_actions": ActionProposal,
        "interpret_task": TaskInterpretation,
        "generate_plan": PlanProposal,
        "analyze_failure": FailureAnalysis,
        "summarize_workflow": WorkflowSummary,
        "choose_action": ActionChoice,
    }
    if operation not in mapping:
        raise KeyError(f"unknown LLM operation {operation!r}; known: {sorted(mapping)}")
    return mapping[operation]


def json_schema(operation: str) -> dict[str, Any]:
    return schema_for(operation).model_json_schema()
