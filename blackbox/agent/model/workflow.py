"""Parameterized reusable workflows mined from verified behavior.

A workflow is not a recording.  It is a goal-shaped sequence of verified
transitions whose literal values have been lifted into named parameters, so it
can be re-instantiated later with different data.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .action import Action
from .base import now_iso, stable_id


class ParameterKind(str, Enum):
    TEXT = "TEXT"
    EMAIL = "EMAIL"
    NUMBER = "NUMBER"
    DATE = "DATE"
    SELECT = "SELECT"
    CHECKBOX = "CHECKBOX"
    FILE = "FILE"
    UNKNOWN = "UNKNOWN"


class WorkflowParameter(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    label: str = ""
    kind: ParameterKind = ParameterKind.TEXT
    required: bool = True
    example_value: str = ""
    target_element_id: str = ""

    def describe(self) -> str:
        return f"{self.name}:{self.kind.value}"


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int
    action: Action
    description: str = ""
    expected_effect: list[str] = Field(default_factory=list)
    transition_id: str | None = None
    optional: bool = False
    # Parameter names this step consumes (e.g. ["name"]).
    parameter_names: list[str] = Field(default_factory=list)

    def describe(self) -> str:
        return f"{self.index}. {self.description or self.action.describe()}"


class Workflow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workflow_id: str = ""
    name: str = ""
    goal: str = ""
    steps: list[WorkflowStep] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    parameters: list[WorkflowParameter] = Field(default_factory=list)
    expected_end_state: str = ""
    start_state_id: str = ""
    path_state_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    verification_count: int = 0
    evidence_ids: list[str] = Field(default_factory=list)
    source: str = "discovered"
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    def finalize(self) -> "Workflow":
        if not self.workflow_id:
            self.workflow_id = stable_id(
                "w", self.goal, [s.action.signature() for s in self.steps]
            )
        return self

    def parameter(self, name: str) -> WorkflowParameter | None:
        for parameter in self.parameters:
            if parameter.name == name:
                return parameter
        return None

    def record_verification(self, evidence_id: str | None = None) -> None:
        self.verification_count += 1
        self.confidence = min(0.99, 0.5 + 0.2 * min(self.verification_count, 3))
        self.updated_at = now_iso()
        if evidence_id and evidence_id not in self.evidence_ids:
            self.evidence_ids.append(evidence_id)

    def describe(self) -> str:
        params = ", ".join(p.describe() for p in self.parameters)
        return f"{self.name or self.goal}({params}) -> {self.expected_end_state} [{len(self.steps)} steps]"


class WorkflowTemplate(BaseModel):
    """Abstract generalized workflow pattern covering multiple entity types.

    Merges structurally similar workflows into reusable parameter templates
    ('create X' rather than only 'create customer').
    """

    model_config = ConfigDict(extra="ignore")

    template_id: str = ""
    name: str = ""
    intent: str = ""  # create | update | export | search | submit | delete
    entity_noun: str = "entity"
    pattern: str = "FORM_SUBMISSION"  # FORM_SUBMISSION | DIRECT_ACTION | NAVIGATION_FLOW
    parameter_templates: list[str] = Field(default_factory=list)
    step_count: int = 0
    concrete_workflow_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    created_at: str = Field(default_factory=now_iso)

    def finalize(self) -> "WorkflowTemplate":
        if not self.template_id:
            self.template_id = stable_id("tmpl", self.intent, self.pattern, self.step_count)
        return self

    def describe(self) -> str:
        return f"{self.name} [{self.pattern}] ({', '.join(self.parameter_templates)})"

