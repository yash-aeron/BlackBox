"""Locator resolution: find an element again, the way a user would.

Locator candidates are tried in descending order of robustness (role+name
first, geometry last).  Resolution happens in the live page, so it survives
re-renders; every attempt reports which strategy matched, how many candidates
were ambiguous, and why, which is what makes locator quality measurable.
"""

from __future__ import annotations

from typing import Any

from blackbox.browser.context import PageContext
from ..model.action import Action
from ..model.base import normalize_text
from ..model.element import Element, LocatorStrategy
from ..observation.network_guard import NetworkGuard

RESOLVE_JS = r"""
((payload) => {
  const candidates = payload.candidates || [];
  const options = payload.options || {};

  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const lower = (s) => norm(s).toLowerCase();

  const INTERACTIVE = [
    'a[href]', 'button', 'input', 'select', 'textarea', 'summary',
    '[role="button"]', '[role="link"]', '[role="tab"]', '[role="menuitem"]',
    '[role="checkbox"]', '[role="radio"]', '[role="switch"]', '[role="combobox"]',
    '[role="searchbox"]', '[role="textbox"]', '[contenteditable="true"]',
    '[onclick]', '[tabindex]:not([tabindex="-1"])'
  ].join(',');

  const all = () => {
    const found = Array.from(document.querySelectorAll(INTERACTIVE));
    const extra = [];
    document.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) extra.push(...el.shadowRoot.querySelectorAll(INTERACTIVE)); });
    return found.concat(extra);
  };

  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const style = window.getComputedStyle(el);
    if (!style) return false;
    return style.display !== 'none' && style.visibility !== 'hidden' && parseFloat(style.opacity || '1') > 0;
  };

  const walkUp = (el) => {
    let node = el;
    let hops = 0;
    while (node && hops < 6) {
      const tag = node.tagName.toLowerCase();
      const role = norm(node.getAttribute && node.getAttribute('role')).toLowerCase();
      if (['button', 'a', 'input', 'select', 'textarea', 'summary'].includes(tag) ||
          ['button', 'link', 'tab', 'menuitem', 'checkbox', 'radio', 'switch', 'combobox', 'searchbox', 'textbox'].includes(role) ||
          (node.hasAttribute && node.hasAttribute('onclick')) || node.isContentEditable) {
        return node;
      }
      node = node.parentElement;
      hops += 1;
    }
    return el;
  };

  const accessibleName = (el) => {
    const attr = (n) => norm(el.getAttribute && el.getAttribute(n));
    if (attr('aria-label')) return attr('aria-label');
    const labelledBy = attr('aria-labelledby');
    if (labelledBy) {
      const parts = labelledBy.split(/\s+/).map((id) => {
        const t = el.ownerDocument.getElementById(id);
        return t ? norm(t.innerText || t.textContent) : '';
      }).filter(Boolean);
      if (parts.length) return parts.join(' ');
    }
    const tag = el.tagName.toLowerCase();
    if (el.id) {
      const label = el.ownerDocument.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (label) return norm(label.innerText || label.textContent);
    }
    const wrapping = el.closest && el.closest('label');
    if (wrapping) {
      const text = norm(wrapping.innerText || wrapping.textContent);
      if (text) return text;
    }
    const placeholder = attr('placeholder');
    if (placeholder) return placeholder;
    const title = attr('title');
    if (title) return title;
    if (tag === 'input' && ['submit', 'button'].includes((attr('type') || '').toLowerCase())) return norm(el.value);
    const text = norm(el.innerText || el.textContent);
    if (text) return text.slice(0, 200);
    return '';
  };

  const roleOf = (el) => {
    const explicit = norm(el.getAttribute && el.getAttribute('role')).toLowerCase();
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    const type = norm(el.getAttribute && el.getAttribute('type')).toLowerCase();
    if (tag === 'button') return 'button';
    if (tag === 'a') return 'link';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (['submit', 'button', 'reset'].includes(type)) return 'button';
      if (type === 'search') return 'searchbox';
      return 'textbox';
    }
    if (el.isContentEditable) return 'textbox';
    return tag;
  };

  const score = (el, candidate) => {
    const params = candidate.params || {};
    const strategy = candidate.strategy;
    const name = accessibleName(el);
    const text = norm(el.innerText || el.textContent).slice(0, 200);
    if (strategy === 'ROLE_NAME') {
      const roleOk = roleOf(el) === lower(params.role) || el.tagName.toLowerCase() === lower(params.role);
      if (!roleOk) return 0;
      const wanted = lower(params.name);
      const actual = lower(name);
      if (actual === wanted) return 1.0;
      if (actual.startsWith(wanted) || wanted.startsWith(actual)) return 0.82;
      if (actual.includes(wanted)) return 0.7;
      return 0;
    }
    if (strategy === 'LABEL') {
      const wanted = lower(params.aria_label || params.label);
      const actual = lower(name);
      if (actual === wanted) return 0.98;
      if (actual.includes(wanted)) return 0.7;
      return 0;
    }
    if (strategy === 'PLACEHOLDER') {
      const ph = lower(el.getAttribute('placeholder'));
      if (!ph) return 0;
      return ph === lower(params.placeholder) ? 0.95 : (ph.includes(lower(params.placeholder)) ? 0.6 : 0);
    }
    if (strategy === 'TEXT') {
      const wanted = lower(params.text);
      if (!wanted) return 0;
      const actual = lower(text);
      if (actual === wanted) return 0.9;
      if (actual.includes(wanted)) return 0.72;
      return 0;
    }
    return 0;
  };

  const results = [];
  for (const candidate of candidates) {
    const strategy = candidate.strategy;
    let matches = [];
    try {
      if (['ELEMENT_ID', 'NAME_ATTR', 'TEST_ID', 'SEMANTIC_ATTR', 'CSS_PATH'].includes(strategy)) {
        const css = (candidate.params && (candidate.params.css || candidate.selector)) || '';
        if (css) matches = Array.from(document.querySelectorAll(css)).map(walkUp);
      } else {
        matches = all().filter((el) => score(el, candidate) > 0);
      }
    } catch (e) { matches = []; }

    const unique = Array.from(new Set(matches));
    const visibleMatches = unique.filter(visible);
    const pool = visibleMatches.length ? visibleMatches : unique;
    if (!pool.length) continue;

    let best = pool[0];
    let bestScore = -1;
    for (const el of pool) {
      const base = strategy === 'COORDINATES' ? 0.5 : (score(el, candidate) || 0.75);
      const inDialog = el.closest('dialog[open], [role="dialog"]') ? 0.03 : 0;
      const enabled = (el.disabled || el.getAttribute('aria-disabled') === 'true') ? -0.15 : 0;
      const total = base + inDialog + enabled;
      if (total > bestScore) { bestScore = total; best = el; }
    }

    const rect = best.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    if (options.require_enabled && (best.disabled || best.getAttribute('aria-disabled') === 'true')) {
      results.push({ matched: false, reason: 'element is disabled', strategy: strategy, ambiguous: pool.length });
      continue;
    }
    // Publish the resolved node for the duration of this action so follow-up
    // operations (clear, read, select) act on exactly the element that was
    // resolved, instead of re-guessing it from text heuristics.
    try { window.__bbTarget = best; } catch (e) { }
    return JSON.stringify({
      matched: true,
      strategy: strategy,
      selector_used: candidate.selector,
      score: Math.round(bestScore * 1000) / 1000,
      ambiguous: pool.length,
      tag: best.tagName.toLowerCase(),
      role: roleOf(best),
      name: accessibleName(best).slice(0, 200),
      value: ('value' in best && typeof best.value === 'string') ? best.value.slice(0, 200) : null,
      checked: typeof best.checked === 'boolean' ? best.checked : null,
      enabled: !(best.disabled || best.getAttribute('aria-disabled') === 'true'),
      rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
      in_viewport: rect.top < window.innerHeight && rect.bottom > 0
    });
  }
  return JSON.stringify({ matched: false, reason: 'no locator candidate matched', tried: candidates.length });
})
"""


class LocatorResolution(dict):
    """Dict result of a resolution attempt (kept a dict for JSON friendliness)."""

    @property
    def matched(self) -> bool:
        return bool(self.get("matched"))

    @property
    def strategy(self) -> str:
        return str(self.get("strategy", ""))

    @property
    def center(self) -> tuple[float, float]:
        rect = self.get("rect") or {}
        return (float(rect.get("x", 0)) + float(rect.get("width", 0)) / 2.0,
                float(rect.get("y", 0)) + float(rect.get("height", 0)) / 2.0)


class LocatorResolver:
    """Resolves element locators in the live page."""

    def __init__(self, *, network_guard: NetworkGuard | None = None) -> None:
        self.network_guard = network_guard

    def candidates_for(self, element: Element | None, action: Action | None = None) -> list[dict[str, Any]]:
        """Collect locator candidates from the action, then the element."""
        candidates: list[dict[str, Any]] = []
        if action and action.target and action.target.locators:
            candidates.extend(action.target.locators)
        if element is not None:
            for candidate in element.locator_candidates:
                payload = {
                    "strategy": candidate.strategy.value,
                    "selector": candidate.selector,
                    "confidence": candidate.confidence,
                    "params": candidate.params,
                }
                if payload not in candidates:
                    candidates.append(payload)
        return candidates

    async def resolve(
        self,
        page: PageContext,
        *,
        element: Element | None = None,
        action: Action | None = None,
        require_enabled: bool = True,
    ) -> LocatorResolution:
        candidates = self.candidates_for(element, action)
        if not candidates:
            return LocatorResolution(matched=False, reason="no locator candidates available")
        # Coordinates always remain available as a documented last resort.
        if element is not None and element.bounding_box is not None:
            x, y = element.bounding_box.center
            if not any(c.get("strategy") == LocatorStrategy.COORDINATES.value for c in candidates):
                candidates.append(
                    {
                        "strategy": LocatorStrategy.COORDINATES.value,
                        "selector": f"point({x:.0f},{y:.0f})",
                        "params": {"x": x, "y": y},
                    }
                )
        return await self.resolve_candidates(page, candidates, require_enabled=require_enabled)

    async def resolve_candidates(
        self,
        page: PageContext,
        candidates: list[dict[str, Any]],
        *,
        require_enabled: bool = True,
    ) -> LocatorResolution:
        payload = {
            "candidates": [
                {
                    "strategy": c.get("strategy"),
                    "selector": c.get("selector", ""),
                    "params": c.get("params", {}),
                }
                for c in candidates
            ],
            "options": {"require_enabled": require_enabled},
        }
        import json

        script = f"({RESOLVE_JS})({json.dumps(payload)})"
        if self.network_guard is not None:
            self.network_guard.check_expression(script, purpose="locator_resolution")
        try:
            raw = await page.evaluate(script)
        except Exception as exc:  # noqa: BLE001 - resolution failure is data, not a crash
            return LocatorResolution(matched=False, reason=f"resolution error: {exc}")
        if not raw:
            return LocatorResolution(matched=False, reason="empty resolution result")
        try:
            return LocatorResolution(json.loads(raw))
        except ValueError:
            return LocatorResolution(matched=False, reason="unparseable resolution result")

    async def scroll_into_view(self, page: PageContext, resolution: LocatorResolution) -> None:
        """Bring the resolved element into the viewport before acting on it."""
        selector_used = resolution.get("selector_used", "")
        strategy = resolution.get("strategy", "")
        script = r"""
        ((payload) => {
          const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
          const target = (() => {
            try {
              if (payload.selector && ['ELEMENT_ID','NAME_ATTR','TEST_ID','SEMANTIC_ATTR','CSS_PATH'].includes(payload.strategy)) {
                return document.querySelector(payload.selector);
              }
            } catch (e) { }
            const wanted = norm(payload.name);
            const nodes = Array.from(document.querySelectorAll('button, a, input, select, textarea, [role="button"], [role="link"], [role="tab"], [role="menuitem"]'));
            return nodes.find((el) => norm(el.getAttribute('aria-label') || el.innerText || el.textContent) === wanted) ||
                   nodes.find((el) => norm(el.innerText || el.textContent).includes(wanted) && wanted.length > 1);
          })();
          if (!target) return false;
          try { target.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) { }
          return true;
        })
        """
        import json

        script = f"({script})({json.dumps({'selector': selector_used, 'strategy': strategy, 'name': resolution.get('name', '')})})"
        try:
            await page.evaluate(script)
        except Exception:  # noqa: BLE001 - scrolling is best effort
            pass
