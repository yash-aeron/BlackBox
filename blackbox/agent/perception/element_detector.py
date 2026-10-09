"""Element detection and locator generation.

Two jobs live here:

1. Decide *which* observed nodes are interactive elements worth acting on, and
   enrich them with accessibility evidence (role, name, disabled, required).
2. Give every element a ranked list of locator candidates, strongest first, so
   execution never depends on a single brittle selector and never falls back to
   hard-coded coordinates until everything else has failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..model.element import (
    STRATEGY_BASE_CONFIDENCE,
    Element,
    ElementRole,
    LocatorCandidate,
    LocatorStrategy,
)
from ..observation.observation import Observation


def _css_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


@dataclass
class DetectionStats:
    total: int = 0
    actionable: int = 0
    unnamed: int = 0
    by_role: dict[str, int] = field(default_factory=dict)
    by_strategy: dict[str, int] = field(default_factory=dict)

    def note(self, element: Element) -> None:
        self.total += 1
        self.by_role[element.semantic_role.value] = self.by_role.get(element.semantic_role.value, 0) + 1
        if not element.label():
            self.unnamed += 1
        if element.locator_candidates:
            self.actionable += 1
            strategy = element.locator_candidates[0].strategy.value
            self.by_strategy[strategy] = self.by_strategy.get(strategy, 0) + 1


ACTIONABLE_ROLES = {
    ElementRole.BUTTON,
    ElementRole.LINK,
    ElementRole.TEXT_FIELD,
    ElementRole.PASSWORD_FIELD,
    ElementRole.CHECKBOX,
    ElementRole.RADIO,
    ElementRole.SELECT,
    ElementRole.TAB,
    ElementRole.MENU_ITEM,
    ElementRole.SEARCH,
    ElementRole.UPLOAD,
    ElementRole.DOWNLOAD,
    ElementRole.PAGINATION,
    ElementRole.OPTION,
    ElementRole.DIALOG,
}


class ElementDetector:
    """Turns an observation into actionable elements with ranked locators."""

    def __init__(self, *, min_box: float = 2.0, max_elements: int = 400) -> None:
        self.min_box = min_box
        self.max_elements = max_elements
        self.stats = DetectionStats()
        self.last_elements: list[Element] = []

    def detect(self, observation: Observation) -> list[Element]:
        ax_index = self._ax_index(observation)
        elements: list[Element] = []
        seen_keys: dict[tuple[str, str, str], Element] = {}

        for element in observation.interactive_elements:
            self._enrich_from_accessibility(element, ax_index)
            element.locator_candidates = self.generate_locators(element)

            # Duplicate controls (identical role+name+section) are common in
            # responsive layouts; keep both but make the duplicate explicit so
            # ranking can avoid ambiguous repeats.
            key = (element.semantic_role.value, element.label().lower(), element.section.lower())
            if key in seen_keys:
                element.attributes["__duplicate_of"] = seen_keys[key].element_id
            else:
                seen_keys[key] = element

            element.confidence = self._confidence(element)
            self.stats.note(element)
            elements.append(element)

        elements.sort(key=lambda e: (-e.confidence, e.semantic_role.value, e.label()))
        self.last_elements = elements[: self.max_elements]
        return self.last_elements

    # -- accessibility fusion ---------------------------------------------
    def _ax_index(self, observation: Observation) -> dict[str, dict[str, Any]]:
        """Index accessibility nodes by normalized (role, name) for enrichment."""
        index: dict[str, dict[str, Any]] = {}
        for node in observation.accessibility_tree.get("nodes", []) or []:
            if node.get("ignored"):
                continue
            role = str(node.get("role", "")).lower()
            name = str(node.get("name", "")).lower()
            index[f"{role}|{name}"] = node
            if name:
                index.setdefault(f"name|{name}", node)
        return index

    def _enrich_from_accessibility(self, element: Element, index: dict[str, dict[str, Any]]) -> None:
        ax_role_map = {
            ElementRole.BUTTON: "button",
            ElementRole.LINK: "link",
            ElementRole.TEXT_FIELD: "textbox",
            ElementRole.SEARCH: "searchbox",
            ElementRole.CHECKBOX: "checkbox",
            ElementRole.RADIO: "radio",
            ElementRole.SELECT: "combobox",
            ElementRole.TAB: "tab",
            ElementRole.MENU_ITEM: "menuitem",
            ElementRole.TABLE: "table",
            ElementRole.DIALOG: "dialog",
        }
        ax_name = element.accessible_name.lower()
        candidates = [
            f"{ax_role_map.get(element.semantic_role, '')}|{ax_name}",
            f"name|{ax_name}" if ax_name else "",
        ]
        node = next((index[c] for c in candidates if c and c in index), None)
        if node is None:
            return
        properties = node.get("properties", {})
        if not element.accessible_name and node.get("name"):
            element.accessible_name = str(node["name"])[:200]
        if properties.get("disabled"):
            element.enabled = False
        if properties.get("required"):
            element.attributes["__required"] = "true"
        if properties.get("invalid") in ("true", True):
            element.attributes["__invalid"] = "true"
        if properties.get("focused"):
            element.focused = True
        if properties.get("expanded") is not None:
            element.attributes["__expanded"] = str(properties["expanded"]).lower()
        element.attributes["__ax_role"] = str(node.get("role", ""))
        element.source = "dom+ax"

    # -- locators ----------------------------------------------------------
    def generate_locators(self, element: Element) -> list[LocatorCandidate]:
        """Build locator candidates in descending order of robustness."""
        candidates: list[LocatorCandidate] = []
        attrs = element.attributes
        name = element.accessible_name or element.visible_text
        label = attrs.get("aria-label") or attrs.get("title") or ""
        placeholder = attrs.get("placeholder") or ""
        tag = element.tag
        role = element.semantic_role.value

        if name:
            candidates.append(
                LocatorCandidate(
                    strategy=LocatorStrategy.ROLE_NAME,
                    selector=f"role={tag or role}[name={name[:80]}]",
                    confidence=STRATEGY_BASE_CONFIDENCE[LocatorStrategy.ROLE_NAME],
                    evidence="accessible name derived from label/aria/text",
                    params={"role": tag or role, "name": name[:120]},
                )
            )
        if label:
            candidates.append(
                LocatorCandidate(
                    strategy=LocatorStrategy.LABEL,
                    selector=f"aria-label={label[:80]}",
                    confidence=STRATEGY_BASE_CONFIDENCE[LocatorStrategy.LABEL],
                    evidence="explicit aria-label/title attribute",
                    params={"aria_label": label[:120]},
                )
            )
        field_label = attrs.get("__label") or attrs.get("for") or ""
        if field_label:
            candidates.append(
                LocatorCandidate(
                    strategy=LocatorStrategy.LABEL,
                    selector=f"label={field_label[:80]}",
                    confidence=STRATEGY_BASE_CONFIDENCE[LocatorStrategy.LABEL] - 0.03,
                    evidence="associated <label> element",
                    params={"label": field_label[:120]},
                )
            )
        if placeholder:
            candidates.append(
                LocatorCandidate(
                    strategy=LocatorStrategy.PLACEHOLDER,
                    selector=f"[placeholder={_css_escape(placeholder[:80])}]",
                    confidence=STRATEGY_BASE_CONFIDENCE[LocatorStrategy.PLACEHOLDER],
                    evidence="placeholder text is visible to the user",
                    params={"placeholder": placeholder[:120]},
                )
            )
        for attr_name in ("data-testid", "data-test-id", "data-test", "data-cy"):
            if attrs.get(attr_name):
                candidates.append(
                    LocatorCandidate(
                        strategy=LocatorStrategy.TEST_ID,
                        selector=f'[{attr_name}="{_css_escape(attrs[attr_name])}"]',
                        confidence=STRATEGY_BASE_CONFIDENCE[LocatorStrategy.TEST_ID],
                        evidence=f"stable test attribute {attr_name}",
                        params={"css": f'[{attr_name}="{_css_escape(attrs[attr_name])}"]'},
                    )
                )
                break
        if attrs.get("id"):
            candidates.append(
                LocatorCandidate(
                    strategy=LocatorStrategy.ELEMENT_ID,
                    selector=f'#{attrs["id"]}',
                    confidence=STRATEGY_BASE_CONFIDENCE[LocatorStrategy.ELEMENT_ID],
                    evidence="element id (re-checked for stability across states)",
                    params={"css": f'#{_css_escape(attrs["id"])}'},
                )
            )
        if attrs.get("name"):
            candidates.append(
                LocatorCandidate(
                    strategy=LocatorStrategy.NAME_ATTR,
                    selector=f'{tag}[name="{_css_escape(attrs["name"])}"]',
                    confidence=STRATEGY_BASE_CONFIDENCE[LocatorStrategy.NAME_ATTR],
                    evidence="form control name attribute",
                    params={"css": f'{tag}[name="{_css_escape(attrs["name"])}"]'},
                )
            )
        if attrs.get("href"):
            candidates.append(
                LocatorCandidate(
                    strategy=LocatorStrategy.SEMANTIC_ATTR,
                    selector=f'a[href="{_css_escape(attrs["href"][:120])}"]',
                    confidence=STRATEGY_BASE_CONFIDENCE[LocatorStrategy.SEMANTIC_ATTR],
                    evidence="link destination",
                    params={"css": f'a[href="{_css_escape(attrs["href"][:120])}"]'},
                )
            )
        if attrs.get("type") and tag == "input":
            candidates.append(
                LocatorCandidate(
                    strategy=LocatorStrategy.SEMANTIC_ATTR,
                    selector=f'input[type="{_css_escape(attrs["type"])}"]',
                    confidence=STRATEGY_BASE_CONFIDENCE[LocatorStrategy.SEMANTIC_ATTR] - 0.1,
                    evidence="input type (weak: many controls share it)",
                    params={"css": f'input[type="{_css_escape(attrs["type"])}"]'},
                )
            )
        if element.visible_text:
            candidates.append(
                LocatorCandidate(
                    strategy=LocatorStrategy.TEXT,
                    selector=f"text={element.visible_text[:80]}",
                    confidence=STRATEGY_BASE_CONFIDENCE[LocatorStrategy.TEXT],
                    evidence="visible text content",
                    params={"text": element.visible_text[:120]},
                )
            )
        if attrs.get("__css_path"):
            candidates.append(
                LocatorCandidate(
                    strategy=LocatorStrategy.CSS_PATH,
                    selector=attrs["__css_path"],
                    confidence=STRATEGY_BASE_CONFIDENCE[LocatorStrategy.CSS_PATH],
                    evidence="structural path (survives text changes, breaks on re-layout)",
                    params={"css": attrs["__css_path"]},
                )
            )
        if element.bounding_box:
            x, y = element.bounding_box.center
            candidates.append(
                LocatorCandidate(
                    strategy=LocatorStrategy.COORDINATES,
                    selector=f"point({x:.0f},{y:.0f})",
                    confidence=STRATEGY_BASE_CONFIDENCE[LocatorStrategy.COORDINATES],
                    evidence="last resort: geometry only, invalidated by any layout change",
                    params={"x": x, "y": y},
                )
            )
        return sorted(candidates, key=lambda c: -c.confidence)

    # -- scoring -----------------------------------------------------------
    def _confidence(self, element: Element) -> float:
        score = 0.4
        if element.label():
            score += 0.2
        if element.locator_candidates and element.locator_candidates[0].strategy in (
            LocatorStrategy.ROLE_NAME,
            LocatorStrategy.LABEL,
            LocatorStrategy.TEST_ID,
        ):
            score += 0.2
        if element.semantic_role in ACTIONABLE_ROLES:
            score += 0.1
        if element.attributes.get("__ax_role"):
            score += 0.05
        if element.attributes.get("__duplicate_of"):
            score -= 0.1
        if not element.enabled:
            score -= 0.05
        return round(min(0.99, max(0.05, score)), 3)
