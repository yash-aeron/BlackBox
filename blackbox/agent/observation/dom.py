"""DOM extraction: the raw material of every observation.

One JavaScript pass walks the rendered document (including shadow roots), and
returns a structured description of what a user could see and touch: text,
interactive elements with geometry and accessible names, forms and their fields,
dialogs, tables, alerts, loading indicators and pagination state.

Nothing here reads framework internals, module state or hidden variables.  The
pass works only with what the browser exposes to any accessibility client.
"""

from __future__ import annotations

import json
from typing import Any

from blackbox.browser.context import PageContext

DOM_EXTRACTION_JS = r"""
(() => {
  const MAX_ELEMENTS = 1200;
  const MAX_TEXT = 20000;

  const refs = [];
  window.__bbRefs = refs;

  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const attr = (el, name) => (el.getAttribute && el.getAttribute(name)) || '';

  const isVisible = (el, rect) => {
    if (!rect || rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(el);
    if (!style) return false;
    if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') return false;
    if (parseFloat(style.opacity || '1') === 0) return false;
    return true;
  };

  const accessibleName = (el) => {
    const aria = norm(attr(el, 'aria-label'));
    if (aria) return aria;
    const labelledBy = attr(el, 'aria-labelledby');
    if (labelledBy) {
      const parts = labelledBy.split(/\s+/).map((id) => {
        const target = el.ownerDocument.getElementById(id);
        return target ? norm(target.innerText || target.textContent) : '';
      }).filter(Boolean);
      if (parts.length) return norm(parts.join(' '));
    }
    const tag = el.tagName.toLowerCase();
    if (tag === 'input' || tag === 'select' || tag === 'textarea') {
      if (el.id) {
        const label = el.ownerDocument.querySelector('label[for="' + CSS.escape(el.id) + '"]');
        if (label) return norm(label.innerText || label.textContent);
      }
      const wrapping = el.closest('label');
      if (wrapping) {
        const text = norm(wrapping.innerText || wrapping.textContent);
        if (text) return text;
      }
      const ph = norm(attr(el, 'placeholder'));
      if (ph) return ph;
    }
    const title = norm(attr(el, 'title'));
    if (title) return title;
    const img = el.querySelector && el.querySelector('img[alt]');
    if (img) {
      const alt = norm(attr(img, 'alt'));
      if (alt) return alt;
    }
    if (tag === 'input' && ['submit', 'button', 'reset'].includes((attr(el, 'type') || '').toLowerCase())) {
      return norm(el.value) || 'Submit';
    }
    const text = norm(el.innerText || el.textContent);
    if (text) return text.slice(0, 160);
    return '';
  };

  const computedRole = (el) => {
    const explicit = norm(attr(el, 'role')).toLowerCase();
    const tag = el.tagName.toLowerCase();
    const type = (attr(el, 'type') || '').toLowerCase();
    if (explicit === 'dialog' || explicit === 'alertdialog') return 'dialog';
    if (explicit) return explicit;
    if (tag === 'button') return 'button';
    if (tag === 'a') return attr(el, 'href') ? 'link' : 'generic';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'dialog') return 'dialog';
    if (tag === 'table') return 'table';
    if (tag === 'form') return 'form';
    if (tag === 'input') {
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'submit' || type === 'button' || type === 'reset' || type === 'image') return 'button';
      if (type === 'search') return 'searchbox';
      if (type === 'file') return 'button';
      if (type === 'range') return 'slider';
      return 'textbox';
    }
    if (el.isContentEditable) return 'textbox';
    return 'generic';
  };

  const elementType = (el) => {
    const tag = el.tagName.toLowerCase();
    const type = (attr(el, 'type') || '').toLowerCase();
    if (tag === 'input') return type || 'text';
    return tag;
  };

  const errorTextFor = (el) => {
    const described = attr(el, 'aria-describedby');
    const parts = [];
    if (described) {
      described.split(/\s+/).forEach((id) => {
        const node = el.ownerDocument.getElementById(id);
        if (node) parts.push(norm(node.innerText || node.textContent));
      });
    }
    const container = el.closest('form, fieldset, .field, .form-field, div');
    if (container) {
      const alert = container.querySelector('[role="alert"], .error, .field-error, .invalid-feedback');
      if (alert) parts.push(norm(alert.innerText || alert.textContent));
    }
    const joined = norm(parts.filter(Boolean).join(' | '));
    return joined.slice(0, 300);
  };

  const sectionFor = (el) => {
    let node = el;
    let hops = 0;
    while (node && hops < 12) {
      let sibling = node.previousElementSibling;
      while (sibling) {
        if (/^h[1-6]$/.test(sibling.tagName)) return norm(sibling.innerText || sibling.textContent).slice(0, 120);
        const inner = sibling.querySelector && sibling.querySelector('h1,h2,h3,h4,h5,h6');
        if (inner) return norm(inner.innerText || inner.textContent).slice(0, 120);
        sibling = sibling.previousElementSibling;
      }
      node = node.parentElement;
      hops += 1;
    }
    return '';
  };

  const dialogFor = (el) => {
    const dialog = el.closest('dialog[open], [role="dialog"], [role="alertdialog"], .modal, .dialog');
    return dialog || null;
  };

  const cssPath = (el) => {
    const parts = [];
    let node = el;
    let depth = 0;
    while (node && node.nodeType === 1 && depth < 6) {
      const tag = node.tagName.toLowerCase();
      if (node.id && /^[A-Za-z][\w-]*$/.test(node.id)) {
        parts.unshift('#' + node.id);
        break;
      }
      let part = tag;
      if (node.parentElement) {
        const siblings = Array.from(node.parentElement.children).filter((c) => c.tagName === node.tagName);
        if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(node) + 1) + ')';
      }
      parts.unshift(part);
      node = node.parentElement;
      depth += 1;
    }
    return parts.join(' > ');
  };

  const formFor = (el) => {
    const form = el.closest('form');
    return form || null;
  };

  const INTERACTIVE = [
    'a[href]', 'button', 'input', 'select', 'textarea', 'summary',
    '[role="button"]', '[role="link"]', '[role="tab"]', '[role="menuitem"]', '[role="menuitemcheckbox"]',
    '[role="menuitemradio"]', '[role="checkbox"]', '[role="radio"]', '[role="switch"]',
    '[role="combobox"]', '[role="searchbox"]', '[role="textbox"]', '[role="slider"]',
    '[contenteditable="true"]', '[onclick]', '[tabindex]:not([tabindex="-1"])'
  ].join(',');

  const collectCandidates = () => {
    const found = [];
    const seen = new Set();
    const walk = (root) => {
      let matched = [];
      try {
        matched = Array.from(root.querySelectorAll(INTERACTIVE));
      } catch (e) { matched = []; }
      for (const el of matched) {
        if (seen.has(el)) continue;
        seen.add(el);
        found.push(el);
      }
      let all = [];
      try { all = Array.from(root.querySelectorAll('*')); } catch (e) { all = []; }
      for (const el of all) {
        if (el.shadowRoot) walk(el.shadowRoot);
      }
    };
    walk(document);
    return found;
  };

  const describe = (el, index) => {
    const rect = el.getBoundingClientRect();
    const attrs = {};
    const whitelist = ['id', 'name', 'type', 'placeholder', 'aria-label', 'aria-labelledby', 'aria-describedby',
      'data-testid', 'data-test-id', 'data-test', 'data-cy', 'title', 'href', 'pattern', 'required', 'disabled',
      'readonly', 'maxlength', 'min', 'max', 'step', 'accept', 'multiple', 'role', 'alt', 'for', 'aria-invalid',
      'aria-expanded', 'aria-checked', 'aria-selected', 'autocomplete', 'aria-busy'];
    for (const key of whitelist) {
      const value = attr(el, key);
      if (value) attrs[key] = String(value).slice(0, 200);
    }
    const type = elementType(el);
    const sensitive = type === 'password';
    let value = null;
    if (!sensitive) {
      if (el.tagName.toLowerCase() === 'select') {
        const opt = el.options ? el.options[el.selectedIndex] : null;
        value = opt ? norm(opt.textContent) : '';
      } else if (type === 'checkbox' || type === 'radio') {
        value = el.checked ? 'checked' : '';
      } else if ('value' in el && typeof el.value === 'string') {
        value = el.value.slice(0, 200);
      }
    }
    const options = [];
    if (el.tagName.toLowerCase() === 'select' && el.options) {
      for (const opt of Array.from(el.options).slice(0, 60)) {
        options.push({ value: opt.value, label: norm(opt.textContent), selected: opt.selected, disabled: opt.disabled });
      }
    }
    const form = formFor(el);
    const dialog = dialogFor(el);
    const rectJson = {
      x: Math.round(rect.x * 10) / 10,
      y: Math.round(rect.y * 10) / 10,
      width: Math.round(rect.width * 10) / 10,
      height: Math.round(rect.height * 10) / 10
    };
    refs.push(el);
    return {
      ref: 'r' + index,
      tag: el.tagName.toLowerCase(),
      type: type,
      role: computedRole(el),
      role_attr: attr(el, 'role'),
      name: accessibleName(el),
      label_text: (() => {
        if (el.id) {
          const label = el.ownerDocument.querySelector('label[for="' + CSS.escape(el.id) + '"]');
          if (label) return norm(label.innerText || label.textContent);
        }
        const wrapping = el.closest('label');
        return wrapping ? norm(wrapping.innerText || wrapping.textContent) : '';
      })(),
      text: norm(el.innerText || el.textContent).slice(0, 200),
      attrs: attrs,
      rect: rectJson,
      in_viewport: rect.top < window.innerHeight && rect.bottom > 0 && rect.left < window.innerWidth && rect.right > 0,
      visible: isVisible(el, rect),
      enabled: !el.disabled && attr(el, 'aria-disabled') !== 'true',
      focused: document.activeElement === el,
      editable: el.isContentEditable || ['input', 'textarea'].includes(el.tagName.toLowerCase()) || el.tagName.toLowerCase() === 'select',
      sensitive: sensitive,
      value: value,
      checked: (type === 'checkbox' || type === 'radio') ? !!el.checked : null,
      required: !!el.required || attr(el, 'aria-required') === 'true',
      invalid: attr(el, 'aria-invalid') === 'true' || (el.matches && el.matches(':invalid')),
      validation_message: (() => {
        try { return (el.validationMessage || '').slice(0, 200); } catch (e) { return ''; }
      })(),
      valid: (() => {
        try { return el.checkValidity ? el.checkValidity() : true; } catch (e) { return true; }
      })(),
      css_path: cssPath(el),
      error_text: errorTextFor(el),
      options: options,
      section: sectionFor(el),
      parent_role: el.parentElement ? computedRole(el.parentElement) : '',
      in_dialog: !!dialog,
      dialog_title: dialog ? norm(dialog.getAttribute('aria-label') || (dialog.querySelector('h1,h2,h3,[role="heading"]') || {}).innerText || '') : '',
      in_form: form ? (form.id || form.getAttribute('name') || 'form-' + Array.from(document.forms).indexOf(form)) : '',
      shadow: !!el.getRootNode().host
    };
  };

  const candidates = collectCandidates();
  const elements = [];
  for (let i = 0; i < candidates.length && elements.length < MAX_ELEMENTS; i++) {
    try { elements.push(describe(candidates[i], i)); } catch (e) { }
  }

  const visibleText = (() => {
    const body = document.body;
    if (!body) return '';
    const text = body.innerText || body.textContent || '';
    return norm(text).slice(0, MAX_TEXT);
  })();

  const describeForm = (form, index) => {
    const fields = [];
    const submitRefs = [];
    Array.from(form.querySelectorAll('input, select, textarea')).forEach((el) => {
      const type = elementType(el);
      const record = elements.find((item) => refs[parseInt(item.ref.slice(1), 10)] === el);
      if (!record) return;
      if (record.role === 'button') { submitRefs.push(record.ref); return; }
      fields.push({
        ref: record.ref,
        name: attr(el, 'name'),
        label: record.name || record.label_text,
        role: record.role,
        type: type,
        required: record.required,
        value: record.value,
        checked: record.checked,
        invalid: record.invalid,
        error_text: record.error_text,
        options: record.options
      });
    });
    Array.from(form.querySelectorAll('button, input[type="submit"], input[type="button"]')).forEach((el) => {
      const record = elements.find((item) => refs[parseInt(item.ref.slice(1), 10)] === el);
      if (record) submitRefs.push(record.ref);
    });
    return {
      form_id: form.id || form.getAttribute('name') || 'form-' + index,
      name: norm(form.getAttribute('name') || form.getAttribute('aria-label') || ''),
      action_hint: norm(form.getAttribute('action') || ''),
      fields: fields,
      submit_refs: submitRefs,
      in_dialog: !!form.closest('dialog[open], [role="dialog"], .modal')
    };
  };

  const forms = Array.from(document.forms).map(describeForm);

  const dialogs = Array.from(document.querySelectorAll('dialog[open], [role="dialog"], [role="alertdialog"]'))
    .filter((node) => isVisible(node, node.getBoundingClientRect()))
    .map((node) => ({
      title: norm(node.getAttribute('aria-label') || (node.querySelector('h1,h2,h3,[role="heading"]') || {}).innerText || ''),
      role: (norm(node.getAttribute('role')) || 'dialog'),
      text: norm(node.innerText || node.textContent).slice(0, 600),
      actions: Array.from(node.querySelectorAll('button, [role="button"], input[type="submit"]'))
        .map((b) => accessibleName(b)).filter(Boolean).slice(0, 12),
      modal: node.tagName.toLowerCase() === 'dialog' ? !!node.hasAttribute('open') : true
    }));

  const tables = Array.from(document.querySelectorAll('table')).slice(0, 20).map((table) => {
    const headers = Array.from(table.querySelectorAll('thead th, tr:first-child th')).map((th) => norm(th.innerText));
    const rows = Array.from(table.querySelectorAll('tbody tr')).slice(0, 30).map((tr) =>
      Array.from(tr.querySelectorAll('td, th')).map((td) => norm(td.innerText).slice(0, 120))
    );
    const record = elements.find((item) => refs[parseInt(item.ref.slice(1), 10)] === table);
    return {
      ref: record ? record.ref : null,
      caption: norm((table.querySelector('caption') || {}).innerText || ''),
      headers: headers,
      row_count: table.querySelectorAll('tbody tr').length,
      rows: rows,
      cell_actions: Array.from(table.querySelectorAll('tbody button, tbody [role="button"]')).map((b) => accessibleName(b)).slice(0, 40)
    };
  });

  const alerts = Array.from(document.querySelectorAll('[role="alert"], [role="status"], [aria-live]'))
    .filter((node) => isVisible(node, node.getBoundingClientRect()))
    .map((node) => ({
      role: norm(node.getAttribute('role')) || 'live',
      text: norm(node.innerText || node.textContent).slice(0, 300),
      ref: (() => { const record = elements.find((item) => refs[parseInt(item.ref.slice(1), 10)] === node); return record ? record.ref : null; })()
    }))
    .filter((item) => item.text);

  const loadingIndicators = alerts.filter((item) => /load|saving|please wait|processing|fetching/i.test(item.text));
  const busy = document.querySelectorAll('[aria-busy="true"]').length;

  const pagination = (() => {
    const text = Array.from(document.querySelectorAll('body *'))
      .filter((el) => el.children.length === 0)
      .map((el) => norm(el.innerText))
      .filter((t) => /^page\s+\d+(\s+of\s+\d+)?$/i.test(t))[0] || '';
    const match = text.match(/page\s+(\d+)\s*(?:of\s*(\d+))?/i);
    const next = Array.from(document.querySelectorAll('button, a[role="button"], [role="button"]'))
      .find((el) => /^next/i.test(accessibleName(el)) && !el.disabled);
    const previous = Array.from(document.querySelectorAll('button, a[role="button"], [role="button"]'))
      .find((el) => /^(previous|prev)/i.test(accessibleName(el)) && !el.disabled);
    return {
      label: text,
      page_index: match ? parseInt(match[1], 10) : null,
      page_count: match && match[2] ? parseInt(match[2], 10) : null,
      has_next: !!next,
      has_previous: !!previous
    };
  })();

  return JSON.stringify({
    url: location.href,
    title: document.title,
    visible_text: visibleText,
    elements: elements,
    forms: forms,
    dialogs: dialogs,
    tables: tables,
    alerts: alerts,
    loading: {
      is_loading: loadingIndicators.length > 0 || busy > 0,
      indicators: loadingIndicators.map((item) => item.text),
      aria_busy: busy
    },
    pagination: pagination,
    counts: {
      interactive: elements.filter((e) => e.visible).length,
      hidden: elements.filter((e) => !e.visible).length,
      shadow_hosts: Array.from(document.querySelectorAll('*')).filter((el) => el.shadowRoot).length,
      iframes: document.querySelectorAll('iframe').length
    },
    iframes: Array.from(document.querySelectorAll('iframe')).map((frame) => ({
      src: (frame.getAttribute('src') || '').slice(0, 200),
      accessible: (() => { try { return !!frame.contentDocument; } catch (e) { return false; } })(),
      rect: (() => { const r = frame.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; })()
    }))
  });
})()
"""


async def extract_dom(page: PageContext) -> dict[str, Any]:
    """Run the DOM extraction pass and return its structured payload."""
    raw = await page.evaluate_json(DOM_EXTRACTION_JS)
    if not isinstance(raw, dict):
        return {
            "url": await page.url(),
            "title": await page.title(),
            "visible_text": "",
            "elements": [],
            "forms": [],
            "dialogs": [],
            "tables": [],
            "alerts": [],
            "loading": {"is_loading": False, "indicators": [], "aria_busy": 0},
            "pagination": {"label": "", "page_index": None, "page_count": None, "has_next": False, "has_previous": False},
            "counts": {"interactive": 0, "hidden": 0, "shadow_hosts": 0, "iframes": 0},
            "iframes": [],
        }
    return raw


async def resolve_ref_box(page: PageContext, ref: str) -> dict[str, Any] | None:
    """Re-read an element's geometry by its observation-time reference."""
    script = f"""
    (() => {{
      const el = (window.__bbRefs || [])[parseInt('{ref}'.slice(1), 10)];
      if (!el) return null;
      const r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) return null;
      return JSON.stringify({{x: r.x, y: r.y, width: r.width, height: r.height}});
    }})()
    """
    try:
        return await page.evaluate_json(script)
    except Exception:  # noqa: BLE001 - stale refs are expected
        return None


def parse_element_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Defensive re-parse for payloads that arrive as JSON strings."""
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except ValueError:
            return {}
    return payload
