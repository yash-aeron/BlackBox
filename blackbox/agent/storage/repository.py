"""Repository: reads and writes the behavioral model.

The graph in memory is the working copy; this layer is how it survives a
restart.  Loading a model reconstructs a live ``ApplicationGraph`` so a later
task can plan against what an earlier session learned - which is the entire
point of the system.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from ..model.base import now_iso
from ..model.constraint import Constraint
from ..model.evidence import Evidence
from ..model.graph import ApplicationGraph
from ..model.page import Page
from ..model.state import StateStatus, WebsiteState
from ..model.transition import Transition, TransitionStatus
from ..model.website import BehavioralModel, HypothesisRecord, ModelVersion, WebsiteIdentity
from ..model.workflow import Workflow
from ..perception.state_fingerprint import StateSimilarity
from ..observation.observation import Observation
from .database import Database, dumps, loads
from .targets import TargetRegistration

log = logging.getLogger(__name__)


class Repository:
    """Persists and restores everything BlackBox has verified."""

    def __init__(self, database: Database, target_id: str, model_version: int = 1) -> None:
        self.db = database
        self.target_id = target_id
        self.model_version = model_version

    # -- targets -----------------------------------------------------------
    def save_target(self, target: TargetRegistration) -> None:
        self.db.upsert(
            "targets",
            {"target_id": target.target_id},
            {
                "base_url": target.base_url,
                "allowed_origins": dumps(target.normalized_origins()),
                "mode": target.mode.value,
                "max_steps": target.max_steps,
                "max_duration_seconds": target.max_duration_seconds,
                "created_at": target.created_at or time.time(),
                "updated_at": time.time(),
                "payload": dumps(target.to_dict()),
            },
        )

    def load_target(self, target_id: str) -> TargetRegistration | None:
        row = self.db.query_one("SELECT payload FROM targets WHERE target_id = ?", (target_id,))
        if not row:
            return None
        return TargetRegistration.model_validate(loads(row["payload"]))

    def list_targets(self) -> list[TargetRegistration]:
        rows = self.db.query("SELECT payload FROM targets ORDER BY target_id")
        return [TargetRegistration.model_validate(loads(row["payload"])) for row in rows]

    # -- model versions ----------------------------------------------------
    def create_model_version(self, *, notes: list[str] | None = None, browser_version: str = "", prompt_version: str = "v1", parent: int | None = None) -> int:
        row = self.db.query_one(
            "SELECT MAX(version) AS version FROM models WHERE target_id = ?", (self.target_id,)
        )
        current = int(row["version"] or 0) if row else 0
        version = current + 1
        self.model_version = version
        self.db.upsert(
            "models",
            {"target_id": self.target_id, "version": version},
            {
                "created_at": time.time(),
                "site_url": None,
                "browser_version": browser_version,
                "prompt_version": prompt_version,
                "parent_version": parent if parent is not None else (current or None),
                "notes": dumps(notes or []),
                "payload": None,
            },
        )
        return version

    def list_model_versions(self) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT version, created_at, browser_version, prompt_version, parent_version, notes "
            "FROM models WHERE target_id = ? ORDER BY version DESC",
            (self.target_id,),
        )

    def latest_version(self) -> int:
        """The newest model version for this target, or 0 if none exists yet."""
        row = self.db.query_one(
            "SELECT MAX(version) AS version FROM models WHERE target_id = ?", (self.target_id,)
        )
        return int(row["version"] or 0) if row else 0

    def use_latest_version(self) -> int:
        """Continue writing into the newest version so learning accumulates."""
        version = self.latest_version()
        if version == 0:
            version = self.create_model_version(notes=["initial model"])
        self.model_version = version
        return version

    # -- writes ------------------------------------------------------------
    def save_state(self, state: WebsiteState, *, similarity: StateSimilarity | None = None) -> None:
        self.db.upsert(
            "states",
            {"state_id": state.state_id},
            {
                "target_id": self.target_id,
                "model_version": self.model_version,
                "url": state.url,
                "url_key": state.fingerprint.url_key,
                "title": state.title,
                "page_id": state.page_id,
                "page_identity": state.page_identity,
                "status": state.status.value,
                "confidence": state.confidence,
                "visit_count": state.visit_count,
                "semantic_summary": state.semantic_summary,
                "screenshot_ref": state.screenshot_ref,
                "fingerprint_key": state.fingerprint.text_signature,
                "first_seen": state.first_seen,
                "last_seen": state.last_seen,
                "payload": dumps(state.model_dump(mode="json")),
            },
        )

    def save_transition(self, transition: Transition) -> None:
        self.db.upsert(
            "transitions",
            {"transition_id": transition.transition_id},
            {
                "target_id": self.target_id,
                "model_version": self.model_version,
                "source_state": transition.source_state,
                "target_state": transition.target_state,
                "action_signature": transition.action.signature(),
                "action_type": transition.action.type.value,
                "risk": transition.action.risk.value,
                "status": transition.status.value,
                "confidence": transition.confidence,
                "execution_count": transition.execution_count,
                "success_count": transition.success_count,
                "failure_count": transition.failure_count,
                "updated_at": transition.updated_at,
                "payload": dumps(transition.model_dump(mode="json")),
            },
        )

    def save_workflow(self, workflow: Workflow) -> None:
        self.db.upsert(
            "workflows",
            {"workflow_id": workflow.workflow_id},
            {
                "target_id": self.target_id,
                "model_version": self.model_version,
                "name": workflow.name,
                "goal": workflow.goal,
                "expected_end_state": workflow.expected_end_state,
                "confidence": workflow.confidence,
                "verification_count": workflow.verification_count,
                "updated_at": workflow.updated_at,
                "payload": dumps(workflow.model_dump(mode="json")),
            },
        )

    def save_constraint(self, constraint: Constraint) -> None:
        self.db.upsert(
            "constraints",
            {"constraint_id": constraint.constraint_id},
            {
                "target_id": self.target_id,
                "model_version": self.model_version,
                "kind": constraint.kind.value,
                "scope": constraint.scope.value,
                "subject": constraint.subject,
                "expression": constraint.expression,
                "message": constraint.message,
                "status": constraint.status.value,
                "confidence": constraint.confidence,
                "updated_at": constraint.updated_at,
                "payload": dumps(constraint.model_dump(mode="json")),
            },
        )

    def save_hypothesis(self, record: HypothesisRecord) -> None:
        self.db.upsert(
            "hypotheses",
            {"hypothesis_id": record.hypothesis_id},
            {
                "target_id": self.target_id,
                "model_version": self.model_version,
                "statement": record.statement,
                "kind": str(record.details.get("kind", "")),
                "status": record.status,
                "confidence": record.confidence,
                "updated_at": record.updated_at,
                "payload": dumps(record.model_dump(mode="json")),
            },
        )

    def save_evidence(self, evidence: Evidence) -> None:
        self.db.upsert(
            "evidence",
            {"evidence_id": evidence.evidence_id},
            {
                "target_id": self.target_id,
                "model_version": self.model_version,
                "kind": evidence.kind.value,
                "experiment_id": evidence.experiment_id,
                "transition_id": evidence.transition_id,
                "source_state": evidence.source_state,
                "target_state": evidence.target_state,
                "message": evidence.message,
