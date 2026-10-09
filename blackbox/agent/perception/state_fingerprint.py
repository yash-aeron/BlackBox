"""State fingerprinting, similarity and the state difference engine.

A state is identified by a bundle of independent signals, never by the URL.
Similarity is a weighted score in [0,1] with configurable thresholds, and the
difference engine explains *what* changed so a transition can be described by
its effects rather than by "something happened".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlparse

from ..model.base import content_hash, jaccard, normalize_text, scrub_volatile, shingles
from ..model.element import Element, ElementRole
from ..model.state import (
    DialogState,
    PaginationState,
    SessionContext,
    StateFingerprint,
    StateSimilarity,
    WebsiteState,
)
from ..observation.observation import Observation
from .semantic_labeler import SemanticLabeler

_ID_SEGMENT = re.compile(r"^(?:\d+|[0-9a-f]{8,}|[0-9a-f-]{16,})$", re.IGNORECASE)
FILTER_ROLES = {ElementRole.SELECT, ElementRole.CHECKBOX, ElementRole.RADIO, ElementRole.SEARCH}


@dataclass
class FingerprintWeights:
    url: float = 0.20
    title: float = 0.06
    text: float = 0.22
    elements: float = 0.24
    accessibility: float = 0.08
    forms: float = 0.05
    controls: float = 0.05
    dialogs: float = 0.06
    screenshot: float = 0.04

    def total(self) -> float:
        return sum(
            [
                self.url,
                self.title,
                self.text,
                self.elements,
                self.accessibility,
                self.forms,
                self.controls,
                self.dialogs,
                self.screenshot,
            ]
        )


@dataclass
class SimilarityThresholds:
    same_state: float = 0.90
    same_page: float = 0.68

    def verdict(self, score: float) -> str:
        if score >= self.same_state:
            return "SAME"
        if score >= self.same_page:
            return "SAME_PAGE"
        return "DIFFERENT"


def url_key(url: str) -> str:
    """Normalize a URL for comparison: mask ids, keep parameter names."""
    parsed = urlparse(url or "")
    segments = []
    for segment in (parsed.path or "/").split("/"):
        if segment and _ID_SEGMENT.match(segment):
            segments.append("{id}")
        else:
            segments.append(segment)
    path = "/".join(segments) or "/"
    params = []
    for name, value in parse_qsl(parsed.query, keep_blank_values=True):
        masked = "{v}" if value else ""
        params.append(f"{name}={masked}" if masked else name)
    query = "&".join(sorted(params))
    fragment = parsed.fragment or ""
    key = f"{parsed.netloc.lower()}{path}"
    if query:
        key += f"?{query}"
    if fragment:
        key += f"#{fragment}"
    return key


def selected_controls(observation: Observation) -> dict[str, str]:
    """Filter-like control values: the difference between two states on one URL."""
    controls: dict[str, str] = {}
    for element in observation.interactive_elements:
        if element.semantic_role in FILTER_ROLES:
            key = normalize_text(element.label())[:60] or element.element_id
            value = element.value_state.value or ("checked" if element.value_state.checked else "")
            if value:
                controls[f"{element.semantic_role.value}:{key}"] = scrub_volatile(str(value))[:60]
    return controls


def form_value_pattern(observation: Observation) -> list[str]:
    """Filled/empty pattern per field.

    Literal values are kept on the state for evidence, but only their presence is
    fingerprinted: otherwise every keystroke would look like a new application
    state.
    """
    pattern: list[str] = []
    for form in observation.forms:
        for field in form.fields:
            filled = "filled" if (field.value or "").strip() or field.checked else "empty"
            pattern.append(f"{normalize_text(field.label or field.name)}:{filled}")
    return sorted(pattern)


def fingerprint_observation(observation: Observation, *, semantic_summary: str = "") -> StateFingerprint:
    text = scrub_volatile(observation.visible_text)
    element_keys = sorted(
        f"{e.semantic_role.value}:{normalize_text(e.label())}" for e in observation.interactive_elements if e.visible
    )
    ax_interactive = observation.accessibility_tree.get("interactive_signature", []) or []
    ax_structure = observation.accessibility_tree.get("structure_signature", []) or []
    dialogs = [DialogState(**d) for d in (dialog.model_dump() for dialog in observation.dialogs)]

    return StateFingerprint(
        url=observation.url,
        url_key=url_key(observation.url),
        title=normalize_text(observation.title),
        text_signature=content_hash(shingles(text))[:16],
        text_shingles=shingles(text),
        element_signature=element_keys[:300],
        accessibility_signature=[normalize_text(s) for s in (ax_interactive or ax_structure)][:300],
        form_signature=form_value_pattern(observation),
        selected_controls=selected_controls(observation),
        dialog_signature=sorted(d.signature() for d in dialogs),
        pagination_signature=PaginationState(
            page_label=str(observation.pagination.get("label") or ""),
            page_index=observation.pagination.get("page_index"),
            page_count=observation.pagination.get("page_count"),
            has_next=bool(observation.pagination.get("has_next")),
            has_previous=bool(observation.pagination.get("has_previous")),
        ).signature(),
        screenshot_hash=observation.screenshot_hash,
        semantic_summary=semantic_summary or observation.title,
    )


def compare_fingerprints(
    left: StateFingerprint,
    right: StateFingerprint,
    *,
    weights: FingerprintWeights | None = None,
    thresholds: SimilarityThresholds | None = None,
) -> StateSimilarity:
    weights = weights or FingerprintWeights()
    thresholds = thresholds or SimilarityThresholds()
    notes: list[str] = []

    url_score = 1.0 if left.url_key == right.url_key else 0.0
    if url_score == 0.0:
        left_path = left.url_key.split("?")[0].split("#")[0]
        right_path = right.url_key.split("?")[0].split("#")[0]
        if left_path and left_path == right_path:
            url_score = 0.75
            notes.append("same path, different query/fragment")

    title_score = 1.0 if left.title == right.title else jaccard(left.title.split(), right.title.split())
    text_score = jaccard(left.text_shingles, right.text_shingles)
    element_score = jaccard(left.element_signature, right.element_signature)
    ax_score = jaccard(left.accessibility_signature, right.accessibility_signature)
    form_score = jaccard(left.form_signature, right.form_signature)

    control_keys = set(left.selected_controls) | set(right.selected_controls)
    if not control_keys:
        control_score = 1.0
    else:
        same = sum(1 for k in control_keys if left.selected_controls.get(k) == right.selected_controls.get(k))
        control_score = same / len(control_keys)

    dialog_score = 1.0 if left.dialog_signature == right.dialog_signature else 0.0
    if left.dialog_signature != right.dialog_signature:
        notes.append("dialog state differs")

    if left.screenshot_hash and right.screenshot_hash:
        from ..observation.screenshot import hamming

        screenshot_score = 1.0 - (hamming(left.screenshot_hash, right.screenshot_hash) / 64.0)
    else:
        screenshot_score = 0.5
        notes.append("no visual evidence")

    signals = {
        "url": url_score,
        "title": title_score,
        "text": text_score,
        "elements": element_score,
        "accessibility": ax_score,
        "forms": form_score,
        "controls": control_score,
        "dialogs": dialog_score,
        "screenshot": screenshot_score,
    }
    weight_map = {
        "url": weights.url,
        "title": weights.title,
        "text": weights.text,
        "elements": weights.elements,
        "accessibility": weights.accessibility,
        "forms": weights.forms,
        "controls": weights.controls,
        "dialogs": weights.dialogs,
        "screenshot": weights.screenshot,
    }
    total_weight = sum(weight_map.values()) or 1.0
    total = sum(signals[name] * weight_map[name] for name in signals) / total_weight

    return StateSimilarity(
        total=round(total, 4),
        same_url=left.url_key == right.url_key,
        signals={k: round(v, 4) for k, v in signals.items()},
        verdict=thresholds.verdict(total),
        notes=notes,
    )


@dataclass
class StateDiff:
    """What changed between two observations, in terms a transition can record."""

    navigation_changed: bool = False
    url_before: str = ""
    url_after: str = ""
    elements_added: list[str] = field(default_factory=list)
    elements_removed: list[str] = field(default_factory=list)
    elements_changed: list[str] = field(default_factory=list)
    text_changes: list[str] = field(default_factory=list)
    dialogs_changed: list[str] = field(default_factory=list)
    form_changes: list[str] = field(default_factory=list)
    semantic_changes: list[str] = field(default_factory=list)
    alerts_added: list[str] = field(default_factory=list)
    alerts_removed: list[str] = field(default_factory=list)
    loading_changed: bool = False
    changed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "navigation_changed": self.navigation_changed,
            "url_before": self.url_before,
            "url_after": self.url_after,
            "elements_added": self.elements_added,
            "elements_removed": self.elements_removed,
            "elements_changed": self.elements_changed,
            "text_changes": self.text_changes,
            "dialogs_changed": self.dialogs_changed,
            "form_changes": self.form_changes,
            "semantic_changes": self.semantic_changes,
            "alerts_added": self.alerts_added,
            "alerts_removed": self.alerts_removed,
            "loading_changed": self.loading_changed,
            "changed": self.changed,
        }

    def is_no_effect(self) -> bool:
        return not any(
            [
                self.navigation_changed,
                self.elements_added,
                self.elements_removed,
                self.elements_changed,
                self.text_changes,
                self.dialogs_changed,
                self.form_changes,
                self.alerts_added,
                self.alerts_removed,
            ]
        )

    def summary(self) -> str:
        bits: list[str] = []
        if self.navigation_changed:
            bits.append(f"navigated to {self.url_after}")
        if self.elements_added:
            bits.append(f"+{len(self.elements_added)} elements")
        if self.elements_removed:
            bits.append(f"-{len(self.elements_removed)} elements")
        if self.elements_changed:
            bits.append(f"{len(self.elements_changed)} elements changed")
        if self.dialogs_changed:
            bits.append("dialog " + ", ".join(self.dialogs_changed))
        if self.form_changes:
            bits.append(f"{len(self.form_changes)} form changes")
        if self.alerts_added:
            bits.append("alert: " + self.alerts_added[0][:80])
        if self.text_changes:
            bits.append(f"text: {self.text_changes[0][:80]}")
        return "; ".join(bits) if bits else "no observable effect"


def _element_map(observation: Observation) -> dict[str, Element]:
    return {e.element_id: e for e in observation.interactive_elements if e.visible}


def _element_state(element: Element) -> tuple[Any, ...]:
    return (
        element.enabled,
        element.value_state.checked,
        element.value_state.value or "",
        element.attributes.get("__invalid") == "true",
        tuple(sorted(element.attributes.get("__options", "").split(","))) if element.attributes.get("__options") else (),
    )


def diff_states(previous: Observation, current: Observation) -> StateDiff:
    """Compare two observations and report every observable difference."""
    before, after = _element_map(previous), _element_map(current)
    diff = StateDiff(
        url_before=previous.url,
        url_after=current.url,
        navigation_changed=url_key(previous.url) != url_key(current.url) or previous.url != current.url,
    )

    for element_id, element in after.items():
        if element_id not in before:
            diff.elements_added.append(f"{element.semantic_role.value}:{element.label()[:60]}")
        elif _element_state(before[element_id]) != _element_state(element):
            diff.elements_changed.append(f"{element.semantic_role.value}:{element.label()[:60]}")
    for element_id, element in before.items():
        if element_id not in after:
            diff.elements_removed.append(f"{element.semantic_role.value}:{element.label()[:60]}")

    previous_dialogs = {d.signature() for d in previous.dialogs}
    current_dialogs = {d.signature() for d in current.dialogs}
    for opened in current_dialogs - previous_dialogs:
        diff.dialogs_changed.append(f"opened {opened}")
    for closed in previous_dialogs - current_dialogs:
        diff.dialogs_changed.append(f"closed {closed}")

    before_forms = {f.form_id: f.state_signature for f in previous.forms}
    after_forms = {f.form_id: f.state_signature for f in current.forms}
    for form_id, signature in after_forms.items():
        if form_id not in before_forms:
            diff.form_changes.append(f"form {form_id} appeared")
        elif before_forms[form_id] != signature:
            changed = _form_field_delta(before_forms[form_id], signature)
            diff.form_changes.append(f"form {form_id}: {changed}")

    previous_alerts = {a.get("text", "") for a in previous.alerts}
    current_alerts = {a.get("text", "") for a in current.alerts}
    diff.alerts_added = sorted(current_alerts - previous_alerts)
    diff.alerts_removed = sorted(previous_alerts - current_alerts)

    diff.loading_changed = previous.loading_state.is_loading != current.loading_state.is_loading

    if scrub_volatile(previous.visible_text) != scrub_volatile(current.visible_text):
        diff.text_changes.append(_text_delta(previous.visible_text, current.visible_text))

    labeler = SemanticLabeler()
    previous_intents = {labeler.label_element(e).intent for e in previous.interactive_elements}
    current_intents = {labeler.label_element(e).intent for e in current.interactive_elements}
    for intent in sorted(i.value for i in current_intents - previous_intents):
        diff.semantic_changes.append(f"new capability: {intent}")
    for intent in sorted(i.value for i in previous_intents - current_intents):
        diff.semantic_changes.append(f"capability gone: {intent}")

    diff.changed = not diff.is_no_effect()
    return diff


def _form_field_delta(before: list[str], after: list[str]) -> str:
    """Name the fields whose values changed, for readable evidence."""
    before_map = {entry.split("=", 1)[0]: entry for entry in before}
    after_map = {entry.split("=", 1)[0]: entry for entry in after}
    changed = [
        key
        for key in after_map
        if key in before_map and before_map[key] != after_map[key]
    ]
    added = [key for key in after_map if key not in before_map]
    removed = [key for key in before_map if key not in after_map]
    parts = []
    if changed:
        parts.append("value changed for " + ", ".join(changed[:5]))
    if added:
        parts.append("fields appeared: " + ", ".join(added[:3]))
    if removed:
        parts.append("fields disappeared: " + ", ".join(removed[:3]))
    return "; ".join(parts) or "field values changed"


def _text_delta(before: str, after: str, *, limit: int = 120) -> str:
    before_words = set(normalize_text(scrub_volatile(before)).split())
    after_words = set(normalize_text(scrub_volatile(after)).split())
    added = [w for w in after_words - before_words][:8]
    removed = [w for w in before_words - after_words][:8]
    parts = []
    if added:
        parts.append("added: " + " ".join(added))
    if removed:
        parts.append("removed: " + " ".join(removed))
    return ("; ".join(parts))[:limit]


def build_state(
    observation: Observation,
    *,
    page_identity: str = "",
    page_id: str = "",
    semantic_summary: str = "",
    session: SessionContext | None = None,
) -> WebsiteState:
    """Assemble a persistable WebsiteState from one observation."""
    fingerprint = fingerprint_observation(observation, semantic_summary=semantic_summary)
    dialogs = [DialogState(**d.model_dump()) for d in observation.dialogs]
    state = WebsiteState(
        fingerprint=fingerprint,
        page_identity=page_identity or fingerprint.url_key,
        page_id=page_id,
        url=observation.url,
        title=observation.title,
        visible_text=observation.visible_text[:20000],
        visible_elements=[e for e in observation.interactive_elements if e.visible],
        forms=observation.forms,
        dialogs=dialogs,
        pagination=PaginationState(
            page_label=str(observation.pagination.get("label") or ""),
            page_index=observation.pagination.get("page_index"),
            page_count=observation.pagination.get("page_count"),
            has_next=bool(observation.pagination.get("has_next")),
            has_previous=bool(observation.pagination.get("has_previous")),
        ),
        session=session or SessionContext(),
        semantic_summary=semantic_summary or observation.title,
        confidence=0.7,
        observation_id=observation.observation_id,
        screenshot_ref=observation.screenshot_reference,
    )
    return state.finalize()


def match_state(
    candidate: WebsiteState,
    states: list[WebsiteState],
    *,
    weights: FingerprintWeights | None = None,
    thresholds: SimilarityThresholds | None = None,
) -> tuple[WebsiteState | None, StateSimilarity | None]:
    """Find the best known state matching a candidate fingerprint."""
    best: tuple[WebsiteState | None, StateSimilarity | None] = (None, None)
    for known in states:
        similarity = compare_fingerprints(candidate.fingerprint, known.fingerprint, weights=weights, thresholds=thresholds)
        if best[1] is None or similarity.total > best[1].total:
            best = (known, similarity)
    if best[1] is not None and best[1].verdict == "DIFFERENT":
        return None, best[1]
    return best
