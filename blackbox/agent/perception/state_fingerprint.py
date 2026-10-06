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
