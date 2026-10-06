"""Page classification and page identity.

Human users think in pages ("the Customers list", "the checkout wizard"), while
the state graph is finer grained.  Pages group states that share a screen, and
the classifier gives each group a navigable name for the learned site map.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlparse

from ..model.base import normalize_text
from ..model.page import Page
from ..model.state import WebsiteState
from ..observation.observation import Observation


class PageKind(str, Enum):
    DASHBOARD = "DASHBOARD"
    LIST = "LIST"
    TABLE = "TABLE"
    DETAIL = "DETAIL"
    FORM = "FORM"
    DIALOG = "DIALOG"
    WIZARD = "WIZARD"
    SETTINGS = "SETTINGS"
    LOGIN = "LOGIN"
    SEARCH_RESULTS = "SEARCH_RESULTS"
    CART = "CART"
    CHECKOUT = "CHECKOUT"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


_STEP = re.compile(r"step\s+\d+\s*(?:of|/)\s*\d+", re.I)
_ID_SEGMENT = re.compile(r"^(?:\d+|[0-9a-f]{8,}|[0-9a-f-]{16,})$", re.I)

KEYWORDS: tuple[tuple[PageKind, tuple[str, ...]], ...] = (
    (PageKind.LOGIN, ("log in", "login", "sign in", "signin", "password")),
    (PageKind.CHECKOUT, ("checkout", "payment", "place order", "billing", "shipping")),
    (PageKind.CART, ("cart", "basket", "bag")),
    (PageKind.SETTINGS, ("settings", "preferences", "configuration", "profile")),
    (PageKind.DASHBOARD, ("dashboard", "overview", "summary", "home")),
)


@dataclass
class PageIdentity:
    page_id: str
    name: str
    url_pattern: str
    kind: PageKind
    section: str = ""
    confidence: float = 0.5
    reasons: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return f"{self.name} [{self.kind.value}] {self.url_pattern}"


def url_pattern(url: str) -> str:
    """Path with concrete ids masked, so all instances of a screen share it."""
    parsed = urlparse(url or "")
    segments = []
    for segment in (parsed.path or "/").split("/"):
        if segment and _ID_SEGMENT.match(segment):
            segments.append("{id}")
        else:
            segments.append(segment)
    path = "/".join(segments) or "/"
    return f"{parsed.netloc.lower()}{path}".strip("/") or path


def _headings(observation: Observation) -> list[str]:
    """Headings visible on the page, largest first (from the DOM channel)."""
    found: list[str] = []
    for node in observation.accessibility_tree.get("nodes", []) or []:
        if str(node.get("role", "")).lower() == "heading" and node.get("name"):
            found.append(str(node["name"]).strip())
    if found:
        return found
    # Fallback: leading lines of the visible text often carry the page title.
    for line in observation.visible_text.split("\n")[:6]:
        line = line.strip()
        if line and len(line) < 60:
            found.append(line)
    return found


def classify(observation: Observation) -> tuple[PageKind, float, list[str]]:
    """Deterministic page classification with the reasons that drove it."""
    text = normalize_text(f"{observation.title} {observation.visible_text[:4000]}")
    reasons: list[str] = []
    password_fields = [e for e in observation.interactive_elements if e.semantic_role.value == "PASSWORD_FIELD"]

    if password_fields and len(observation.interactive_elements) <= 12:
        return PageKind.LOGIN, 0.8, ["password field with few other controls"]

    if _STEP.search(text):
        return PageKind.WIZARD, 0.85, ["step indicator present"]

    open_dialogs = [d for d in observation.dialogs if d.modal]
    if open_dialogs:
        detail = "; ".join(d.title or d.text[:60] for d in open_dialogs)
        return PageKind.DIALOG, 0.85, [f"modal dialog open: {detail}"]

    tables = [t for t in observation.tables if t.row_count >= 1]
    if tables and observation.pagination.get("label"):
        return PageKind.SEARCH_RESULTS, 0.75, ["paginated table with page indicator"]
    if tables:
        return PageKind.TABLE, 0.8, [f"table with {tables[0].row_count} rows"]

    for kind, words in KEYWORDS:
        for word in words:
            if word in text:
                if kind is PageKind.DASHBOARD and observation.forms:
                    continue
                reasons.append(f"keyword {word!r}")
                return kind, 0.65, reasons

    if observation.forms:
        fields = sum(len(f.fields) for f in observation.forms)
        kind = PageKind.FORM if fields >= 2 else PageKind.DETAIL
        return kind, 0.6, [f"{len(observation.forms)} form(s) with {fields} field(s)"]

    links = [e for e in observation.interactive_elements if e.semantic_role.value == "LINK"]
    if len(links) >= 5:
        return PageKind.LIST, 0.5, [f"{len(links)} links suggesting a list"]

    path = url_pattern(observation.url)
    if "{id}" in path:
        return PageKind.DETAIL, 0.5, ["id-like path segment with no table or form"]

    return PageKind.UNKNOWN, 0.3, ["no strong signal"]


def page_name(observation: Observation, kind: PageKind) -> tuple[str, str]:
    """Return (name, section) for the page."""
    headings = _headings(observation)
    title = (observation.title or "").strip()
    name = ""
    for heading in headings:
        if heading and len(heading) < 60:
            name = heading
            break
    section = ""
    for heading in headings[1:4]:
        if heading and heading != name:
            section = heading
            break
    if not name:
        name = title or url_pattern(observation.url).split("/")[-1] or "unknown"
    name = re.sub(r"\s*[|·—-]\s*.*$", "", name).strip() if "|" in name else name
    return name[:80], section[:80]


def identity_for(observation: Observation) -> PageIdentity:
    kind, confidence, reasons = classify(observation)
    name, section = page_name(observation, kind)
    pattern = url_pattern(observation.url)
    from ..model.base import stable_id

    return PageIdentity(
        page_id=stable_id("p", pattern or name, kind.value),
        name=name,
        url_pattern=pattern,
        kind=kind,
        section=section,
        confidence=confidence,
        reasons=reasons,
    )


def to_page(identity: PageIdentity, state_id: str, *, summary: str = "") -> Page:
    page = Page(
        page_id=identity.page_id,
        name=identity.name,
        url_pattern=identity.url_pattern,
        section=identity.section,
        state_ids=[state_id],
        summary=summary,
    )
    return page.finalize()


def navigation_targets(observation: Observation) -> list[str]:
    """Labels of controls that look like navigation, for the learned site map."""
    targets: list[str] = []
    for element in observation.interactive_elements:
        role = element.semantic_role.value
        if role in ("LINK", "TAB", "MENU_ITEM", "BUTTON"):
            label = element.label()
            if label and label not in targets:
                targets.append(label[:60])
    return targets


def assign_page(state: WebsiteState, identity: PageIdentity) -> WebsiteState:
    state.page_identity = identity.url_pattern or identity.name
    state.page_id = identity.page_id
    return state
