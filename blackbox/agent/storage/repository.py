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
                "created_at": evidence.created_at,
                "payload": dumps(evidence.model_dump(mode="json")),
            },
        )

    def save_experiment(self, record: Any) -> None:
        payload = record.to_dict() if hasattr(record, "to_dict") else dict(record)
        self.db.upsert(
            "experiments",
            {"experiment_id": payload["experiment_id"]},
            {
                "target_id": payload.get("target_id", self.target_id),
                "kind": payload.get("kind", "exploration"),
                "seed": payload.get("seed"),
                "browser_version": payload.get("browser_version"),
                "model_version": payload.get("model_version"),
                "prompt_version": payload.get("prompt_version"),
                "status": payload.get("status"),
                "started_at": payload.get("started_at"),
                "finished_at": payload.get("finished_at"),
                "metrics": dumps(payload.get("metrics", {})),
                "payload": dumps(payload),
            },
        )

    def save_observation_meta(self, observation: Observation, state_id: str | None = None) -> None:
        self.db.upsert(
            "observations",
            {"observation_id": observation.observation_id},
            {
                "target_id": self.target_id,
                "state_id": state_id,
                "url": observation.url,
                "title": observation.title,
                "element_count": len(observation.interactive_elements),
                "captured_ms": observation.captured_ms,
                "screenshot_ref": observation.screenshot_reference,
                "created_at": time.time(),
                "payload": dumps(observation.summary()),
            },
        )

    def save_page(self, page: Page) -> None:
        self.db.upsert(
            "pages",
            {"page_id": page.page_id},
            {
                "target_id": self.target_id,
                "name": page.name,
                "url_pattern": page.url_pattern,
                "section": page.section,
                "parent_page_id": page.parent_page_id,
                "payload": dumps(page.model_dump(mode="json")),
            },
        )

    def save_event(self, kind: str, payload: dict[str, Any], *, session_id: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO events (target_id, session_id, kind, created_at, payload) VALUES (?, ?, ?, ?, ?)",
            (self.target_id, session_id, kind, time.time(), dumps(payload)),
        )

    def read_events(self, *, limit: int = 200, kind: str | None = None) -> list[dict[str, Any]]:
        if kind:
            rows = self.db.query(
                "SELECT * FROM events WHERE target_id = ? AND kind = ? ORDER BY event_id DESC LIMIT ?",
                (self.target_id, kind, limit),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM events WHERE target_id = ? ORDER BY event_id DESC LIMIT ?",
                (self.target_id, limit),
            )
        for row in rows:
            row["payload"] = loads(row["payload"])
        return rows

    def clear_workflows(self, *, model_version: int | None = None) -> None:
        """Drop mined workflows so a re-mine replaces them instead of accumulating."""
        version = model_version or self.model_version
        self.db.execute(
            "DELETE FROM workflows WHERE target_id = ? AND model_version = ?", (self.target_id, version)
        )

    def mark_stale(self, *, state_ids: list[str], transition_ids: list[str], reason: str = "") -> dict[str, int]:
        """Mark affected knowledge STALE when the website has changed.

        Old knowledge is never deleted: it stays as historical evidence, and the
        counters below say how much of the model was affected.
        """
        marked_states = 0
        for state_id in state_ids:
            row = self.db.query_one(
                "SELECT payload FROM states WHERE state_id = ? AND target_id = ?", (state_id, self.target_id)
            )
            if not row:
                continue
            state = WebsiteState.model_validate(loads(row["payload"]))
            state.status = StateStatus.STALE
            self.save_state(state)
            marked_states += 1
        marked_transitions = 0
        for transition_id in transition_ids:
            row = self.db.query_one(
                "SELECT payload FROM transitions WHERE transition_id = ? AND target_id = ?",
                (transition_id, self.target_id),
            )
            if not row:
                continue
            transition = Transition.model_validate(loads(row["payload"]))
            transition.status = TransitionStatus.STALE
            self.save_transition(transition)
            marked_transitions += 1
        if reason:
            self.save_event("model_stale", {"reason": reason, "states": marked_states, "transitions": marked_transitions})
        return {"states": marked_states, "transitions": marked_transitions}

    def start_run(self, run_id: str, kind: str) -> None:
        self.db.upsert(
            "runs",
            {"run_id": run_id},
            {
                "target_id": self.target_id,
                "kind": kind,
                "status": "running",
                "started_at": time.time(),
                "finished_at": None,
                "summary": None,
            },
        )

    def finish_run(self, run_id: str, status: str, summary: dict[str, Any] | None = None) -> None:
        self.db.upsert(
            "runs",
            {"run_id": run_id},
            {
                "target_id": self.target_id,
                "kind": None,
                "status": status,
                "started_at": None,
                "finished_at": time.time(),
                "summary": dumps(summary or {}),
            },
        )

    # -- reads -------------------------------------------------------------
    def load_graph(self, *, model_version: int | None = None) -> ApplicationGraph:
        version = model_version or self.model_version
        graph = ApplicationGraph(target_id=self.target_id)
        for row in self.db.query(
            "SELECT payload FROM states WHERE target_id = ? AND model_version = ?", (self.target_id, version)
        ):
            state = WebsiteState.model_validate(loads(row["payload"]))
            graph.states[state.state_id] = state
        for row in self.db.query(
            "SELECT payload FROM transitions WHERE target_id = ? AND model_version = ?", (self.target_id, version)
        ):
            transition = Transition.model_validate(loads(row["payload"]))
            graph.transitions[transition.transition_id] = transition
        for row in self.db.query(
            "SELECT payload FROM workflows WHERE target_id = ? AND model_version = ?", (self.target_id, version)
        ):
            workflow = Workflow.model_validate(loads(row["payload"]))
            graph.workflows[workflow.workflow_id] = workflow
        for row in self.db.query(
            "SELECT payload FROM constraints WHERE target_id = ? AND model_version = ?", (self.target_id, version)
        ):
            constraint = Constraint.model_validate(loads(row["payload"]))
            graph.constraints[constraint.constraint_id] = constraint
        for row in self.db.query(
            "SELECT payload FROM evidence WHERE target_id = ? AND model_version = ?", (self.target_id, version)
        ):
            evidence = Evidence.model_validate(loads(row["payload"]))
            graph.evidence[evidence.evidence_id] = evidence
        for row in self.db.query("SELECT payload FROM pages WHERE target_id = ?", (self.target_id,)):
            page = Page.model_validate(loads(row["payload"]))
            graph.pages[page.page_id] = page
        if graph.states and graph.root_state_id is None:
            first = min(graph.states.values(), key=lambda s: s.first_seen)
            graph.root_state_id = first.state_id
        return graph

    def load_model(self, *, model_version: int | None = None) -> BehavioralModel:
        version = model_version or self.model_version
        graph = self.load_graph(model_version=version)
        target = self.load_target(self.target_id)
        model = BehavioralModel(
            website=WebsiteIdentity(
                target_id=self.target_id,
                base_url=target.base_url if target else "",
                allowed_origins=target.normalized_origins() if target else [],
                root_title=next((s.title for s in graph.states.values()), ""),
            ).finalize(),
            model_version=ModelVersion(version=version),
            states=list(graph.states.values()),
            transitions=list(graph.transitions.values()),
            workflows=list(graph.workflows.values()),
            constraints=list(graph.constraints.values()),
            pages=list(graph.pages.values()),
            evidence=list(graph.evidence.values()),
        )
        rows = self.db.query(
            "SELECT payload FROM hypotheses WHERE target_id = ? AND model_version = ?", (self.target_id, version)
        )
        model.hypotheses = [HypothesisRecord.model_validate(loads(row["payload"])) for row in rows]
        experiments = self.db.query(
            "SELECT metrics, payload FROM experiments WHERE target_id = ? ORDER BY started_at DESC LIMIT 50",
            (self.target_id,),
        )
        model.experiments = [loads(row["payload"]) for row in experiments]
        model.stats = self.stats()
        return model

    def stats(self) -> dict[str, Any]:
        def count(table: str, where: str = "target_id = ?", params: tuple[Any, ...] = ()) -> int:
            row = self.db.query_one(
                f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", (self.target_id, *params)
            )
            return int(row["n"]) if row else 0

        verified_row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM transitions WHERE target_id = ? AND status = ?",
            (self.target_id, "VERIFIED"),
        )
        return {
            "target_id": self.target_id,
            "model_version": self.model_version,
            "states": count("states"),
            "transitions": count("transitions"),
            "verified_transitions": int(verified_row["n"]) if verified_row else 0,
            "workflows": count("workflows"),
            "constraints": count("constraints"),
            "hypotheses": count("hypotheses"),
            "evidence": count("evidence"),
            "experiments": count("experiments"),
            "pages": count("pages"),
        }

    # -- export / import ---------------------------------------------------
    def export_model(self, path: str | Path, *, model_version: int | None = None) -> Path:
        model = self.load_model(model_version=model_version)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            __import__("json").dumps(model.to_json(), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return destination

    def import_model(self, path: str | Path, *, as_new_version: bool = True) -> BehavioralModel:
        import json

        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        model = BehavioralModel.model_validate(payload)
        version = self.create_model_version(notes=[f"imported from {Path(path).name}"]) if as_new_version else self.model_version
        for state in model.states:
            self.save_state(state)
        for transition in model.transitions:
            self.save_transition(transition)
        for workflow in model.workflows:
            self.save_workflow(workflow)
        for constraint in model.constraints:
            self.save_constraint(constraint)
        for evidence in model.evidence:
            self.save_evidence(evidence)
        for page in model.pages:
            self.save_page(page)
        for hypothesis in model.hypotheses:
            self.save_hypothesis(hypothesis)
        self.model_version = version
        return model

    def latest_similarity_notes(self) -> dict[str, Any]:
        return {"loaded_at": now_iso(), "states": self.stats()["states"]}
