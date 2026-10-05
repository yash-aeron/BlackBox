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
