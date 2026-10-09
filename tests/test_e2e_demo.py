"""End-to-end tests against the bundled demo websites.

Marked ``e2e``; they skip cleanly when the demo servers are not running.  Every
run uses its own temporary model database, so the real learned model is never
touched, and budgets are deliberately small.

What these tests protect is the honesty of the model:

* every transition points at a state that was really observed;
* nothing is VERIFIED without evidence, or with a zero success rate;
* risky actions are gated rather than silently executed;
* the explorer records evidence for the actions it takes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blackbox.agent.config import Settings, demo_registration
from blackbox.agent.main import BlackBoxAgent

pytestmark = pytest.mark.e2e


async def explore(target: str, tmp_path: Path, *, max_actions: int = 12) -> tuple[BlackBoxAgent, object]:
    settings = Settings(
        database_dsn=f"sqlite:///{tmp_path / (target + '.db')}",
        artifacts_dir=tmp_path / target,
        headless=True,
        capture_screenshots=False,
        unattended=True,
    )
    agent = BlackBoxAgent(settings, demo_registration(target, mode="AUTONOMOUS"))
    await agent.start()
    result = await agent.explore(
        agent.default_exploration_config(max_actions=max_actions, max_seconds=180, configuration_label="e2e")
    )
    return agent, result


async def test_crm_exploration_produces_a_sound_model(demo_crm_or_skip, tmp_path):
    agent, result = await explore("demo_crm", tmp_path, max_actions=12)
    try:
        assert result.actions_executed > 0
        assert result.stop_reason
        assert agent.graph.states, "exploration must record states"
        assert agent.graph.transitions, "exploration must record transitions"

        for transition in agent.graph.transitions.values():
            assert transition.target_state in agent.graph.states, "a transition must point at an observed state"
            if transition.status.value == "VERIFIED":
                assert transition.evidence_ids, "a verified transition must cite evidence"
                assert not (transition.execution_count > 0 and transition.success_count == 0)
            assert transition.source_state in agent.graph.states

        assert agent.graph.evidence, "actions must produce evidence"
        stats = agent.repository.stats()
        assert stats["states"] >= 1 and stats["transitions"] >= 1
        assert stats["model_version"] >= 1
    finally:
        await agent.close()


async def test_crm_model_persists_and_reloads(demo_crm_or_skip, tmp_path):
    agent, _ = await explore("demo_crm", tmp_path, max_actions=10)
    target_id = agent.registration.target_id
    states = len(agent.graph.states)
    transitions = len(agent.graph.transitions)
    version = agent.repository.model_version
    settings = agent.settings
    await agent.close()

    reopened = BlackBoxAgent(settings, demo_registration(target_id, mode="AUTONOMOUS"))
    try:
        assert len(reopened.graph.states) >= 1, "a restart must load the learned model"
        assert reopened.repository.model_version == version, "a restart continues the same model version"
        assert len(reopened.graph.states) <= states + 1
        assert len(reopened.graph.transitions) >= min(1, transitions)
    finally:
        reopened.database.close()


async def test_risky_actions_are_gated_not_executed(demo_crm_or_skip, tmp_path):
    agent, result = await explore("demo_crm", tmp_path, max_actions=25)
    try:
        critical = [
            transition
            for transition in agent.graph.transitions.values()
            if transition.action.risk.rank >= 2 and transition.execution_count > 0
        ]
        assert not critical, "HIGH/CRITICAL actions must not execute in an unattended run"
        if result.actions_blocked:
            assert result.approvals_requested > 0 or result.actions_blocked > 0
        policy = agent.status().to_dict()["policy"]
        assert policy["allow_high_actions"] is False
        assert policy["allow_critical_actions"] is False
    finally:
        await agent.close()


async def test_forms_exploration_observes_wizard_structure(demo_forms_or_skip, tmp_path):
    agent, result = await explore("demo_forms", tmp_path, max_actions=14)
    try:
        assert agent.graph.states
        text = " ".join(state.visible_text for state in agent.graph.states.values()).lower()
        assert "applicant" in text or "employment" in text or "step 1 of 4" in text or "documents" in text
        assert result.actions_executed > 0
    finally:
        await agent.close()
