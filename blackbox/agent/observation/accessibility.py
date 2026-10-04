"""Accessibility-tree observation.

The accessibility tree is the browser's own opinion about what is on the page:
roles, accessible names, and states like disabled/expanded/required/invalid.
BlackBox treats it as one channel among several - never the sole truth - but it
is often the channel that survives a beauty redesign that breaks the DOM shape.
"""

from __future__ import annotations

from typing import Any

from blackbox.browser.context import PageContext


def _value(node: dict[str, Any], key: str) -> Any:
    raw = node.get(key)
    if isinstance(raw, dict):
        return raw.get("value")
    return raw


def normalize_ax_tree(raw_nodes: list[dict[str, Any]], *, max_nodes: int = 3000) -> dict[str, Any]:
    """Turn CDP AX nodes into a compact, fingerprint-friendly structure."""
    by_id: dict[str, dict[str, Any]] = {}
    for node in raw_nodes:
        node_id = str(node.get("nodeId", ""))
        if not node_id:
            continue
        properties: dict[str, Any] = {}
        for prop in node.get("properties", []) or []:
            name = prop.get("name")
            if name:
                properties[name] = _value(prop, "value")
        by_id[node_id] = {
            "node_id": node_id,
            "role": (_value(node, "role") or "").lower(),
            "name": (_value(node, "name") or "")[:200],
            "description": (_value(node, "description") or "")[:200],
            "ignored": bool(node.get("ignored", False)),
            "backend_node_id": node.get("backendDOMNodeId"),
            "child_ids": [str(c) for c in (node.get("childIds") or [])],
            "properties": properties,
        }

    roots = [node for node in by_id.values() if node["role"] in ("rootwebarea", "webarea")]
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(node_id: str, depth: int) -> None:
        if node_id in seen or len(ordered) >= max_nodes:
            return
        node = by_id.get(node_id)
        if node is None:
            return
        seen.add(node_id)
        node["depth"] = depth
        ordered.append(node)
        for child in node["child_ids"]:
            walk(child, depth + 1)

    for root in roots or list(by_id.values())[:1]:
        walk(root["node_id"], 0)

    # Include any nodes not reachable from the root (broken child links).
    for node_id, node in by_id.items():
        if node_id not in seen and len(ordered) < max_nodes:
            node["depth"] = 0
            ordered.append(node)

    interactive_roles = {
        "button",
        "link",
        "textbox",
        "searchbox",
        "checkbox",
        "radio",
        "combobox",
        "listbox",
        "tab",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "slider",
        "switch",
        "spinbutton",
    }
    signature = [f"{node['role']}:{node['name']}" for node in ordered if not node["ignored"] and node["role"] in interactive_roles]
    structure = [node["role"] for node in ordered if not node["ignored"]][:400]

    return {
        "nodes": ordered[:max_nodes],
        "interactive_signature": signature[:400],
        "structure_signature": structure,
        "node_count": len(ordered),
        "available": bool(ordered),
    }


async def extract_accessibility(page: PageContext) -> dict[str, Any]:
    """Fetch and normalize the accessibility tree, degrading gracefully."""
    try:
        raw = await page.send("Accessibility.getFullAXTree", {"depth": -1}, timeout=30.0)
    except Exception:  # noqa: BLE001 - accessibility may be unavailable
        return {"nodes": [], "interactive_signature": [], "structure_signature": [], "node_count": 0, "available": False}
    return normalize_ax_tree(raw.get("nodes", []) or [])


def ax_index_by_backend_node(tree: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Map accessibility nodes back onto DOM elements for evidence fusion."""
    index: dict[int, dict[str, Any]] = {}
    for node in tree.get("nodes", []):
        backend = node.get("backend_node_id")
        if backend:
            index[int(backend)] = node
    return index
