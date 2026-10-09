"""Tests for Limitations 1 and 2:
- Active learning & exploration coverage (hypothesis persistence, form sequencing, frontier navigation)
- Outcome-driven value & constraint inference (parsing validation messages and constraint-guided value synthesis)
"""

from __future__ import annotations

import pytest

from blackbox.agent.exploration.action_generator import ActionGenerator, ValueSynthesizer
from blackbox.agent.exploration.action_ranker import ActionRanker
from blackbox.agent.exploration.novelty import NoveltyTracker
from blackbox.agent.learning.hypothesis import Hypothesis, HypothesisEngine, HypothesisKind, HypothesisStatus
from blackbox.agent.learning.preconditions import PreconditionLearner
from blackbox.agent.model.action import Action, ActionType, EffectKind
from blackbox.agent.model.constraint import Constraint, ConstraintKind
from blackbox.agent.model.element import Element, ElementRole, ValueState
from blackbox.agent.model.graph import ApplicationGraph
from blackbox.agent.model.state import WebsiteState
from blackbox.agent.model.transition import Transition, TransitionStatus
from blackbox.agent.observation.observation import Observation

from .conftest import action, element, observation


# ---------------------------------------------------------------------------
# Limitation 1: Active Learning & Exploration Coverage
# ---------------------------------------------------------------------------


def test_hypothesis_stays_active_across_observations_until_filled():
    """Hypotheses proposed on an earlier observation remain eligible in for_hypothesis
    as long as the target field is visible and empty, even if observation_id changed."""
    name_input = element("Name *", ElementRole.TEXT_FIELD, tag="input", editable=True)
    obs1 = observation(elements=[name_input, element("Save", ElementRole.BUTTON)], text="Frame 1")
    
    engine = HypothesisEngine()
    hypotheses = engine.propose_requirements(obs1, [name_input, element("Save", ElementRole.BUTTON, enabled=False)])
    assert len(hypotheses) >= 1
    h = hypotheses[0]
    
    generator = ActionGenerator()
    
    # Obs2 is a newly captured frame with a different observation_id
    name_input_empty = element("Name *", ElementRole.TEXT_FIELD, tag="input", editable=True)
    obs2 = observation(elements=[name_input_empty, element("Save", ElementRole.BUTTON)], text="Frame 2")
    assert obs2.observation_id != obs1.observation_id
    
    # for_hypothesis should match by element_id / label even across observation frames
    actions = generator.for_hypothesis(h, obs2, [name_input_empty])
    assert len(actions) == 1
    assert actions[0].type is ActionType.TYPE
    assert actions[0].target.name == name_input_empty.label()


def test_form_sequential_completion_ranked_above_nav():
    """In an active form or dialog, filling unfilled fields in that form is prioritized
    over navigating away or clicking random navigation links."""
    name_field = element("Name *", ElementRole.TEXT_FIELD, tag="input", editable=True, in_dialog=True)
    email_field = element("Email *", ElementRole.TEXT_FIELD, tag="input", editable=True, in_dialog=True)
    save_btn = element("Save", ElementRole.BUTTON, enabled=False, in_dialog=True)
    nav_link = element("Dashboard", ElementRole.LINK, tag="a")
    
    obs = observation(elements=[name_field, email_field, save_btn, nav_link])
    st = WebsiteState(state_id="s_crm_dialog", url=obs.url, visible_elements=obs.interactive_elements).finalize()
    
    generator = ActionGenerator()
    candidates = generator.generate(obs, elements=obs.interactive_elements, state_id=st.state_id)
    
    ranker = ActionRanker()
    novelty = NoveltyTracker()
    
    ranked = ranker.rank(
        candidates,
        state=st,
        novelty=novelty,
        element_lookup={e.element_id: e for e in obs.interactive_elements},
        active_form_element_ids={name_field.element_id, email_field.element_id, save_btn.element_id},
    )
    
    top_targets = [r.action.target.name for r in ranked if r.action.target]
    # The fields inside the active dialog/form should rank higher than the outside nav link
    assert "Dashboard" not in top_targets[:2]
    assert any(target in ("Name *", "Email *") for target in top_targets[:2])


def test_frontier_state_detection_in_graph():
    """The graph / explorer identifies frontier states (states having untried actions)
    and can propose navigation to the frontier state when the current state is exhausted."""
    from blackbox.agent.exploration.explorer import find_frontier_state
    
    graph = ApplicationGraph()
    s_home = WebsiteState(state_id="s_home", url="http://test/").finalize()
    s_form = WebsiteState(state_id="s_form", url="http://test/form").finalize()
    graph.upsert_state(s_home)
    graph.upsert_state(s_form)
    
    act_to_form = action(ActionType.CLICK, element("Open Form"))
    t = Transition(source_state="s_home", target_state="s_form", action=act_to_form, status=TransitionStatus.VERIFIED).finalize()
    graph.add_transition(t)
    
    novelty = NoveltyTracker()
    # s_home is exhausted
    novelty.note_action("s_home", act_to_form, target_state="s_form", effects=1)
    
    # s_form has an untried action
    frontier = find_frontier_state(graph, novelty, current_state_id="s_home", candidate_actions_per_state={
        "s_home": [act_to_form],
        "s_form": [action(ActionType.CLICK, element("Submit"))],
    })
    assert frontier == "s_form"


# ---------------------------------------------------------------------------
# Limitation 2: Outcome-Driven Value & Constraint Inference
# ---------------------------------------------------------------------------


def test_precondition_learner_extracts_rich_constraints():
    """PreconditionLearner parses length, prefix, range, and format constraints from validation messages."""
    learner = PreconditionLearner()
    obs = observation(elements=[element("Username"), element("Promo Code"), element("Age")])
    act = action(ActionType.TYPE, element("Username"))
    
    # Min length
    c1 = learner.from_blocking_message(act, "Username must be at least 8 characters", obs)
    assert len(c1) == 1
    assert c1[0].condition.get("min_length") == 8
    
    # Max length
    c2 = learner.from_blocking_message(act, "Username must be at most 20 characters", obs)
    assert len(c2) == 1
    assert c2[0].condition.get("max_length") == 20
    
    # Prefix
    c3 = learner.from_blocking_message(act, "Promo Code must start with SAVE", obs)
    assert len(c3) == 1
    assert c3[0].condition.get("prefix") == "SAVE"
    
    # Numeric range
    c4 = learner.from_blocking_message(act, "Age must be between 18 and 65", obs)
    assert len(c4) == 1
    assert c4[0].condition.get("min") == 18
    assert c4[0].condition.get("max") == 65


def test_value_synthesizer_adapts_to_learned_constraints():
    """ValueSynthesizer uses learned constraints to produce satisfying values."""
    synthesizer = ValueSynthesizer()
    text_elem = element("Username", ElementRole.TEXT_FIELD, tag="input", editable=True)
    
    # Min length constraint
    val_long = synthesizer.synthesize(text_elem, constraints={"min_length": 15})
    assert len(val_long) >= 15
    
    # Prefix constraint
    promo_elem = element("Promo Code", ElementRole.TEXT_FIELD, tag="input", editable=True)
    val_promo = synthesizer.synthesize(promo_elem, constraints={"prefix": "SAVE"})
    assert val_promo.startswith("SAVE")
    
    # Numeric range constraint
    num_elem = element("Age", ElementRole.TEXT_FIELD, tag="input", editable=True, attributes={"type": "number"})
    val_num = synthesizer.synthesize(num_elem, constraints={"min": 25, "max": 40})
    assert 25 <= int(val_num) <= 40
    
    # Rejected values avoidance
    val_avoid = synthesizer.synthesize(text_elem, constraints={"rejected_values": ["BlackBox Sample"]})
    assert val_avoid != "BlackBox Sample"
