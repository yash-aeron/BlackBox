"""Unit tests for the deterministic core: fingerprints, elements, safety, graph,
planning, persistence and metrics.  No browser is launched here."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blackbox.agent.execution.assertions import (
    Predicate,
    PredicateKind,
    AssertionEngine,
    verify_effects,
)
from blackbox.agent.execution.recovery import FailureKind, RecoveryManager, RecoveryStrategy
from blackbox.agent.exploration.action_generator import ActionGenerator, ValueSynthesizer
from blackbox.agent.exploration.action_ranker import ActionRanker
from blackbox.agent.exploration.novelty import NoveltyTracker
from blackbox.agent.exploration.safety import (
    ApprovalBroker,
    ApprovalRequest,
    ExecutionMode,
    RiskLevel,
    SafetyClassifier,
    SafetyPolicy,
)
from blackbox.agent.learning.causal import CausalEngine
from blackbox.agent.learning.confidence import ConfidenceModel, age_days
from blackbox.agent.learning.effects import EffectExtractor, postcondition_for
from blackbox.agent.learning.hypothesis import Hypothesis, HypothesisEngine, HypothesisStatus
from blackbox.agent.learning.preconditions import PreconditionLearner
from blackbox.agent.learning.workflow_miner import WorkflowMiner
from blackbox.agent.llm.provider import NullProvider, build_provider
from blackbox.agent.llm.schemas import ActionProposal, schema_for
from blackbox.agent.model.action import Action, ActionType, EffectKind, ObservedEffect, RiskLevel as ModelRisk
from blackbox.agent.model.base import normalize_text, scrub_volatile, shingles, stable_id
from blackbox.agent.model.constraint import Constraint, ConstraintKind, ConstraintScope
from blackbox.agent.model.element import Element, ElementRole, FormField, FormSummary, LocatorStrategy
from blackbox.agent.model.evidence import Evidence, EvidenceKind
from blackbox.agent.model.graph import ApplicationGraph, CostModel
from blackbox.agent.model.state import StateFingerprint, WebsiteState
from blackbox.agent.model.transition import Transition, TransitionStatus
from blackbox.agent.model.website import BehavioralModel, WebsiteIdentity
from blackbox.agent.model.workflow import Workflow, WorkflowParameter, WorkflowStep
from blackbox.agent.observation.network_guard import NetworkAccessBlocked, NetworkGuard
from blackbox.agent.observation.observation import DialogSummary
from blackbox.agent.perception.element_detector import ElementDetector
from blackbox.agent.perception.page_classifier import PageKind, classify, identity_for, url_pattern
from blackbox.agent.perception.state_fingerprint import (
    FingerprintWeights,
    SimilarityThresholds,
    build_state,
    compare_fingerprints,
    diff_states,
    fingerprint_observation,
    match_state,
    url_key,
)
from blackbox.agent.planning.planner import Planner
from blackbox.agent.planning.replanner import ReplanKind, Replanner
from blackbox.agent.planning.task_parser import TaskParser
from blackbox.agent.planning.verifier import Verifier
from blackbox.agent.storage.database import Database
from blackbox.agent.storage.repository import Repository
from blackbox.agent.storage.targets import TargetRegistration
from blackbox.browser.sandbox import OriginViolation, Sandbox, origin_of

from .conftest import action, element, observation


# --------------------------------------------------------------- fingerprints


def test_url_key_masks_ids_and_sorts_params():
    assert url_key("http://x.test/customers/42?b=2&a=1") == url_key("http://x.test/customers/99?a=1&b=2")
    assert "42" not in url_key("http://x.test/customers/42")
    assert url_key("http://x.test/orders/abc123def456").endswith("{id}")


def test_scrub_volatile_masks_timestamps_and_tokens():
    left = "Updated 12:31:04 by user 550e8400-e29b-41d4-a716-446655440000 ref 123456789"
    right = "Updated 09:02:59 by user 550e8400-e29b-41d4-a716-446655440001 ref 987654321"
    assert scrub_volatile(left) == scrub_volatile(right)


def test_fingerprint_is_stable_but_distinguishes_filters_and_dialogs():
    plain = observation()
    same_again = observation()
    filtered = observation(elements=[element("Add customer"), element("Lead", ElementRole.SELECT, tag="select")], text="Customers Lead")
    with_dialog = observation(dialogs=[DialogSummary(title="Add customer", role="dialog", actions=["Save", "Cancel"])])

    a = fingerprint_observation(plain)
    b = fingerprint_observation(same_again)
    c = fingerprint_observation(filtered)
    d = fingerprint_observation(with_dialog)

    assert a.canonical() == b.canonical()
    assert a.canonical() != c.canonical()
    assert a.canonical() != d.canonical()
    assert a.url_key == c.url_key == d.url_key, "the URL is identical; only the state differs"


def test_similarity_thresholds_and_verdicts():
    weights, thresholds = FingerprintWeights(), SimilarityThresholds()
    base = fingerprint_observation(observation())
    assert compare_fingerprints(base, base, weights=weights, thresholds=thresholds).verdict == "SAME"

    near = fingerprint_observation(observation(text="Customers Name Email Company Status Revenue Add customer row"))
    verdict = compare_fingerprints(base, near, weights=weights, thresholds=thresholds).verdict
    assert verdict in ("SAME", "SAME_PAGE")

    far = fingerprint_observation(
        observation(
            url="http://127.0.0.1:3001/#/settings",
            title="Settings",
            text="Settings Email notifications Default page size Save settings",
            elements=[element("Save settings")],
            screenshot_hash="ffffffffffffffff",
        )
    )
    assert compare_fingerprints(base, far, weights=weights, thresholds=thresholds).verdict == "DIFFERENT"


def test_match_state_returns_none_for_unrelated_state():
    known = build_state(observation(), semantic_summary="customers")
    other = build_state(
        observation(url="http://127.0.0.1:3001/#/settings", title="Settings", text="Settings only", elements=[element("Save settings")]),
        semantic_summary="settings",
    )
    matched, similarity = match_state(other, [known])
    assert matched is None
    assert similarity is not None and similarity.verdict == "DIFFERENT"


def test_diff_states_reports_effects_and_form_value_changes():
    before = observation(elements=[element("Name", ElementRole.TEXT_FIELD, tag="input", editable=True, value="")])
    after = observation(
        elements=[element("Name", ElementRole.TEXT_FIELD, tag="input", editable=True, value="Yash")],
        alerts=[],
    )
    diff = diff_states(before, after)
    assert diff.changed and not diff.is_no_effect()
    assert any("value changed" in change for change in diff.form_changes) or diff.elements_changed

    identical = diff_states(before, observation(elements=[element("Name", ElementRole.TEXT_FIELD, tag="input", editable=True, value="")]))
    assert identical.is_no_effect()

    alerting = diff_states(before, observation(alerts=[{"role": "alert", "text": "Enter a valid email address."}]))
    assert alerting.alerts_added == ["Enter a valid email address."]


# ------------------------------------------------------------ elements + locators


def test_element_identity_is_content_addressed():
    first = element("Save customer")
    second = element("Save customer")
    assert first.element_id == second.element_id
    assert first.identity_key() == second.identity_key()


def test_element_detector_generates_locators_strongest_first():
    rich = element(
        "Save customer",
        attributes={"id": "customer-save", "name": "save", "data-testid": "save-customer", "__css_path": "form > button"},
        box=(5.0, 6.0, 80.0, 20.0),
    )
    detector = ElementDetector()
    candidates = detector.generate_locators(rich)
    strategies = [candidate.strategy for candidate in candidates]
    assert strategies[0] is LocatorStrategy.ROLE_NAME
    assert LocatorStrategy.TEST_ID in strategies
    assert LocatorStrategy.ELEMENT_ID in strategies
    assert LocatorStrategy.COORDINATES in strategies
    assert strategies[-1] is LocatorStrategy.COORDINATES
    assert candidates[-1].confidence < candidates[0].confidence
    assert all(candidate.evidence for candidate in candidates)


def test_element_detector_enriches_from_accessibility_and_flags_duplicates():
    detector = ElementDetector()
    save = element("Save customer", attributes={})
    duplicate = element("Save customer", attributes={})
    first = observation(elements=[save])
    first.accessibility_tree = {
        "nodes": [
            {
                "node_id": "1",
                "role": "button",
                "name": "Save customer",
                "ignored": False,
                "properties": {"disabled": True, "required": True},
            }
        ],
        "available": True,
    }
    detected = detector.detect(first)
    assert detected[0].enabled is False
    assert detected[0].attributes.get("__required") == "true"
    assert detected[0].source == "dom+ax"

    second = observation(elements=[save, duplicate])
    detected_two = detector.detect(second)
    assert any(e.attributes.get("__duplicate_of") for e in detected_two)


def test_role_mapping_from_raw_dom():
    from blackbox.agent.observation.fusion import role_for_element

    assert role_for_element({"tag": "button", "text": "Save"}) is ElementRole.BUTTON
    assert role_for_element({"tag": "a", "attrs": {}, "text": "x"}) is ElementRole.LINK
    assert role_for_element({"tag": "input", "type": "password"}) is ElementRole.PASSWORD_FIELD
    assert role_for_element({"tag": "input", "type": "file"}) is ElementRole.UPLOAD
    assert role_for_element({"tag": "input", "type": "search"}) is ElementRole.SEARCH
    assert role_for_element({"tag": "select"}) is ElementRole.SELECT
    assert role_for_element({"tag": "input", "type": "email"}) is ElementRole.TEXT_FIELD
    assert role_for_element({"tag": "button", "text": "Export report"}) is ElementRole.DOWNLOAD
    assert role_for_element({"tag": "dialog"}) is ElementRole.DIALOG


def test_form_signature_separates_structure_from_values():
    form = FormSummary(
        form_id="customer-form",
        fields=[FormField(element_id="e1", label="Name", value="", role=ElementRole.TEXT_FIELD)],
    )
    structure = form.signature
    values = form.state_signature
    form.fields[0].value = "Yash"
    assert form.signature == structure, "structure drives state identity"
    assert form.state_signature != values, "values drive change detection"


# ------------------------------------------------------------- safety + guard


def test_safety_classifies_destructive_controls_and_data_entry():
    classifier = SafetyClassifier()
    delete = element("Delete customer")
    assert classifier.classify(action(ActionType.CLICK, delete), element=delete).risk is RiskLevel.HIGH

    email_field = element("Email *", ElementRole.TEXT_FIELD, tag="input", editable=True)
    typing_email = classifier.classify(
        action(ActionType.TYPE, email_field, value="a@b.com"), element=email_field
    )
    assert typing_email.risk is RiskLevel.LOW, "an Email field is data, not a send action"

    password = element("Password", ElementRole.PASSWORD_FIELD, tag="input", editable=True)
    assert classifier.classify(action(ActionType.TYPE, password, value="x"), element=password).risk is RiskLevel.HIGH

    pay = element("Place order")
    assert classifier.classify(action(ActionType.CLICK, pay), element=pay).risk is RiskLevel.CRITICAL

    link = element("Customers", ElementRole.LINK, tag="a")
    assert classifier.classify(action(ActionType.CLICK, link), element=link).risk is RiskLevel.LOW


def test_safety_escalates_inside_a_confirmation_dialog():
    classifier = SafetyClassifier()
    confirm = element("Delete customer", in_dialog=True)
    dialog = observation(dialogs=[DialogSummary(title="Delete customer?", role="dialog", text="This cannot be undone.", actions=["Cancel", "Delete customer"])])
    assessment = classifier.classify(action(ActionType.CLICK, confirm), element=confirm, observation=dialog)
    assert assessment.risk is RiskLevel.CRITICAL
    assert assessment.in_confirmation_dialog


def test_safety_policy_modes():
    safe = SafetyPolicy(mode=ExecutionMode.SAFE)
    supervised = SafetyPolicy(mode=ExecutionMode.SUPERVISED)
    permissive = SafetyPolicy(mode=ExecutionMode.SUPERVISED, allow_medium_with_config=True)
    autonomous = SafetyPolicy(mode=ExecutionMode.AUTONOMOUS)
    assert safe.permits(RiskLevel.LOW) and not safe.permits(RiskLevel.MEDIUM)
    assert supervised.permits(RiskLevel.LOW) and not supervised.permits(RiskLevel.MEDIUM)
    assert permissive.permits(RiskLevel.MEDIUM) and not permissive.permits(RiskLevel.CRITICAL)
    assert autonomous.permits(RiskLevel.MEDIUM) and not autonomous.permits(RiskLevel.CRITICAL)


def test_sandbox_blocks_other_origins_and_records_the_reason():
    sandbox = Sandbox(allowed_origins=["http://127.0.0.1:3001"])
    assert sandbox.is_allowed("http://127.0.0.1:3001/#/customers").allowed
    assert sandbox.is_allowed("data:text/html,<p>x</p>").allowed
