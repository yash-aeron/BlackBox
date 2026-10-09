"""Observation fusion: DOM + accessibility + visual + browser state into one record.

Each channel is kept, with its own health, because they disagree in useful ways:
a canvas control has geometry but no DOM semantics, an aria-only widget has
semantics but no text, and a screenshot catches both when rendering lies.
"""

from __future__ import annotations

from typing import Any

from ..model.element import Element, ElementRole, FormField, FormSummary
from .observation import (
    BrowserState,
    DialogSummary,
    LinkSummary,
    LoadingState,
    Observation,
    TableSummary,
    Viewport,
)


def _role_from_string(value: str) -> ElementRole:
    key = (value or "").strip().upper().replace("-", "_").replace(" ", "_")
    if not key:
        return ElementRole.UNKNOWN
    if key in ElementRole.__members__:
        return ElementRole[key]
    aliases = {
        "GENERIC": ElementRole.TEXT,
        "STATICTEXT": ElementRole.TEXT,
        "TEXTBOX": ElementRole.TEXT_FIELD,
        "SEARCHBOX": ElementRole.SEARCH,
        "COMBOBOX": ElementRole.SELECT,
        "LISTBOX": ElementRole.SELECT,
        "OPTION": ElementRole.OPTION,
        "SWITCH": ElementRole.CHECKBOX,
        "SLIDER": ElementRole.UNKNOWN,
        "ALERT": ElementRole.TEXT,
        "HEADING": ElementRole.TEXT,
        "PARAGRAPH": ElementRole.TEXT,
        "ROW": ElementRole.TABLE_ROW,
        "CELL": ElementRole.TABLE_CELL,
        "GRID": ElementRole.TABLE,
        "LISTITEM": ElementRole.MENU_ITEM,
        "MENUITEMCHECKBOX": ElementRole.MENU_ITEM,
        "MENUITEMRADIO": ElementRole.MENU_ITEM,
        "SUBMIT": ElementRole.BUTTON,
        "PUSHBUTTON": ElementRole.BUTTON,
        "IMAGE": ElementRole.UNKNOWN,
        "IMG": ElementRole.UNKNOWN,
        "IFRAME": ElementRole.UNKNOWN,
        "FORM": ElementRole.FORM,
        "DIALOG": ElementRole.DIALOG,
        "TABLE": ElementRole.TABLE,
        "LINK": ElementRole.LINK,
        "BUTTON": ElementRole.BUTTON,
        "CHECKBOX": ElementRole.CHECKBOX,
        "RADIO": ElementRole.RADIO,
        "TAB": ElementRole.TAB,
        "MENU": ElementRole.MENU,
        "MENUITEM": ElementRole.MENU_ITEM,
        "PAGINATION": ElementRole.PAGINATION,
        "UPLOAD": ElementRole.UPLOAD,
        "DOWNLOAD": ElementRole.DOWNLOAD,
        "PASSWORD": ElementRole.PASSWORD_FIELD,
    }
    return aliases.get(key, ElementRole.UNKNOWN)


def role_for_element(raw: dict[str, Any]) -> ElementRole:
    """Map a raw DOM record to a BlackBox role, preferring explicit semantics."""
    tag = (raw.get("tag") or "").lower()
    input_type = (raw.get("type") or "").lower()
    role = (raw.get("role") or "").lower()
    name = ((raw.get("name") or "") + " " + (raw.get("text") or "")).lower()

    if tag == "input":
        if input_type == "password":
            return ElementRole.PASSWORD_FIELD
        if input_type == "file":
            return ElementRole.UPLOAD
        if input_type == "search" or role == "searchbox":
            return ElementRole.SEARCH
        if input_type == "checkbox":
            return ElementRole.CHECKBOX
        if input_type == "radio":
            return ElementRole.RADIO
        if input_type in ("submit", "button", "reset", "image"):
            return ElementRole.DOWNLOAD if "download" in name or "export" in name else ElementRole.BUTTON
        if input_type in ("text", "email", "tel", "url", "number", "date", "datetime-local", "month", "week", "time", ""):
            return ElementRole.TEXT_FIELD
        return ElementRole.TEXT_FIELD
    if tag == "textarea":
        return ElementRole.TEXT_FIELD
    if tag == "select":
        return ElementRole.SELECT
    if tag == "option":
        return ElementRole.OPTION
    if tag == "button":
        if "download" in name or "export" in name:
            return ElementRole.DOWNLOAD
        return ElementRole.BUTTON
    if tag == "a":
        return ElementRole.LINK
    if tag == "form":
        return ElementRole.FORM
    if tag == "table":
        return ElementRole.TABLE
    if tag == "dialog":
        return ElementRole.DIALOG
    if role in ("dialog", "alertdialog"):
        return ElementRole.DIALOG
    if role in ("tab", "tablist"):
        return ElementRole.TAB
    if role == "menu":
        return ElementRole.MENU
    if role in ("menuitem", "menuitemcheckbox", "menuitemradio", "option", "listitem"):
        return ElementRole.MENU_ITEM
    if role == "pagination" or "page " in name:
        return ElementRole.PAGINATION
    return _role_from_string(role) if role else ElementRole.UNKNOWN


def fuse_observation(
    *,
    dom: dict[str, Any],
    ax_tree: dict[str, Any],
    screenshot_ref: str | None,
    screenshot_hash: str,
    browser_state: BrowserState,
    viewport: Viewport,
    counts: dict[str, Any],
    captured_ms: float,
    screenshot_note: str = "",
) -> Observation:
    """Assemble the multi-channel observation record."""
    elements: list[Element] = []
    for raw in dom.get("elements", []) or []:
        element = build_element(raw)
        if element is not None:
            elements.append(element)

    forms: list[FormSummary] = []
    for raw_form in dom.get("forms", []) or []:
        fields = [
            FormField(
                element_id=_element_id_for(raw.get("ref"), elements, raw),
                name=raw.get("name") or "",
                label=raw.get("label") or "",
                role=_role_from_string(raw.get("role") or ""),
                required=bool(raw.get("required")),
                value=raw.get("value") or "",
                checked=raw.get("checked"),
                options=[o.get("label", "") for o in raw.get("options", []) or []],
            )
            for raw in raw_form.get("fields", []) or []
        ]
        forms.append(
            FormSummary(
                form_id=raw_form.get("form_id") or "form",
                name=raw_form.get("name") or "",
                action_hint=raw_form.get("action_hint") or "",
                fields=fields,
                submit_element_ids=[
                    _ref_to_element_id(ref, elements) for ref in raw_form.get("submit_refs", []) or []
                ],
                in_dialog=bool(raw_form.get("in_dialog")),
            )
        )

    dialogs = [
        DialogSummary(
            title=raw.get("title") or "",
            role=raw.get("role") or "dialog",
            text=raw.get("text") or "",
            actions=raw.get("actions") or [],
            modal=bool(raw.get("modal", True)),
        )
        for raw in dom.get("dialogs", []) or []
    ]

    tables = [
        TableSummary(
            element_id=_ref_to_element_id(raw.get("ref"), elements) if raw.get("ref") else None,
            caption=raw.get("caption") or "",
            headers=raw.get("headers") or [],
            row_count=int(raw.get("row_count") or 0),
            rows=raw.get("rows") or [],
            cell_actions=raw.get("cell_actions") or [],
        )
        for raw in dom.get("tables", []) or []
    ]

    links = [
        LinkSummary(element_id=element.element_id, text=element.accessible_name, href=element.attributes.get("href", ""), external=False)
        for element in elements
        if element.semantic_role is ElementRole.LINK
    ]

    loading_raw = dom.get("loading", {}) or {}
    observation = Observation(
        url=dom.get("url") or browser_state.url,
        title=dom.get("title") or browser_state.title,
        viewport=viewport,
        screenshot_reference=screenshot_ref,
        screenshot_hash=screenshot_hash,
        visible_text=dom.get("visible_text") or "",
        interactive_elements=elements,
        accessibility_tree=ax_tree,
        forms=forms,
        dialogs=dialogs,
        tables=tables,
        links=links,
        alerts=dom.get("alerts") or [],
        iframes=dom.get("iframes") or [],
        browser_state=browser_state,
        loading_state=LoadingState(
            is_loading=bool(loading_raw.get("is_loading")),
            indicators=loading_raw.get("indicators") or [],
            aria_busy=int(loading_raw.get("aria_busy") or 0),
        ),
        pagination=dom.get("pagination") or {},
        counts=counts,
        channel_health={
            "dom": bool(elements),
            "accessibility": bool(ax_tree.get("available")),
            "visual": screenshot_ref is not None,
            "visual_note": screenshot_note,
            "native_dialogs": len(browser_state.native_dialogs),
        },
        captured_ms=captured_ms,
    )
    return observation.finalize()


def build_element(raw: dict[str, Any]) -> Element | None:
    """Convert one raw DOM record into a normalized Element."""
    from ..model.element import BoundingBox

    rect = raw.get("rect") or {}
    box = None
    if rect.get("width") or rect.get("height"):
        box = BoundingBox(
            x=float(rect.get("x", 0.0)),
            y=float(rect.get("y", 0.0)),
            width=float(rect.get("width", 0.0)),
            height=float(rect.get("height", 0.0)),
        )
    role = role_for_element(raw)
    visible = bool(raw.get("visible"))
    element = Element(
        semantic_role=role,
        visible_text=(raw.get("text") or "")[:200],
        accessible_name=(raw.get("name") or "")[:200],
        tag=raw.get("tag") or "",
        attributes={k: v for k, v in (raw.get("attrs") or {}).items()},
        bounding_box=box,
        enabled=bool(raw.get("enabled", True)),
        visible=visible,
        focused=bool(raw.get("focused")),
        editable=bool(raw.get("editable")),
        value_state={
            "value": None if raw.get("sensitive") else raw.get("value"),
            "checked": raw.get("checked"),
            "disabled_reason": None,
        },
        confidence=0.6,
        source="dom",
        snapshot_ref=raw.get("ref"),
        in_dialog=bool(raw.get("in_dialog")),
        in_form=raw.get("in_form") or None,
        section=raw.get("section") or "",
    )
    if raw.get("options"):
        element.attributes["__options"] = ",".join(
            str(option.get("label", "")) for option in raw["options"] if option.get("label")
        )
    if raw.get("sensitive"):
        element.attributes["__sensitive"] = "true"
    if raw.get("invalid"):
        element.attributes["__invalid"] = "true"
    if raw.get("error_text"):
        element.attributes["__error_text"] = str(raw["error_text"])[:200]
    if raw.get("required"):
        element.attributes["__required"] = "true"
    if raw.get("in_viewport") is False:
        element.attributes["__offscreen"] = "true"
    if raw.get("css_path"):
        element.attributes["__css_path"] = str(raw["css_path"])[:300]
    if raw.get("validation_message"):
        element.attributes["__validation_message"] = str(raw["validation_message"])[:200]
    if raw.get("valid") is False:
        element.attributes["__valid"] = "false"
    if raw.get("label_text"):
        element.attributes["__label"] = str(raw["label_text"])[:160]
    if raw.get("dialog_title"):
        element.attributes["__dialog_title"] = str(raw["dialog_title"])[:120]
    return element.finalize()


def _ref_to_element_id(ref: str | None, elements: list[Element]) -> str:
    if not ref:
        return ""
    for element in elements:
        if element.snapshot_ref == ref:
            return element.element_id
    return ""


def _element_id_for(ref: str | None, elements: list[Element], raw: dict[str, Any]) -> str:
    return _ref_to_element_id(ref, elements) or f"e_unknown_{abs(hash(str(ref))) % 10**8}"
