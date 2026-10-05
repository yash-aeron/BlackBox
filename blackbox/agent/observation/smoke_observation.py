"""Smoke test: observation pipeline against an inline page."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

from blackbox.agent.observation.browser import PageObserver
from blackbox.agent.observation.network_guard import NetworkGuard
from blackbox.browser.manager import BrowserManager
from blackbox.browser.permissions import default_policy
from blackbox.browser.sandbox import Sandbox

HTML = """
<!doctype html><html><head><title>Customers</title></head><body>
<nav><a href="#/dashboard">Dashboard</a> <a href="#/customers">Customers</a></nav>
<h1>Customers</h1>
<label for="q">Search customers</label><input id="q" type="search" placeholder="Search">
<select id="status" name="status"><option>All</option><option>Active</option></select>
<table><thead><tr><th>Name</th><th>Email</th></tr></thead>
<tbody><tr><td>Ada</td><td>ada@example.com</td></tr></tbody></table>
<button id="add">Add customer</button>
<button id="save" disabled>Save</button>
<form id="cust"><label for="nm">Name</label><input id="nm" name="name" required>
<label for="em">Email</label><input id="em" name="email" type="email" required>
<button type="submit">Create</button></form>
<div role="alert" style="display:none"></div>
<script>
document.getElementById('cust').addEventListener('submit', (e) => {
  e.preventDefault();
  const alertBox = document.querySelector('[role=alert]');
  if (!document.getElementById('em').value.includes('@')) {
    alertBox.style.display = 'block';
    alertBox.textContent = 'Enter a valid email address.';
    document.getElementById('em').setAttribute('aria-invalid', 'true');
    return;
  }
  alertBox.style.display = 'block';
  alertBox.textContent = 'Customer created.';
});
</script></body></html>
"""


async def main() -> int:
    artifacts = Path("artifacts/smoke")
    artifacts.mkdir(parents=True, exist_ok=True)
    sandbox = Sandbox(allowed_origins=["http://127.0.0.1"])
    manager = BrowserManager(
        sandbox=sandbox,
        permissions=default_policy(artifacts / "downloads"),
        artifacts_dir=artifacts,
        headless=True,
    )
    page = await manager.start()
    observer = PageObserver(artifacts, network_guard=NetworkGuard(allowed_origins=["http://127.0.0.1"]))
    try:
        url = "data:text/html;base64," + base64.b64encode(HTML.encode()).decode()
        await page.navigate(url)
        observation = await observer.observe(page)
        print(json.dumps(observation.summary(), indent=2))
        print("channel health:", json.dumps(observation.channel_health))
        print("counts:", json.dumps(observation.counts))
        print("forms:", len(observation.forms), "dialogs:", len(observation.dialogs), "tables:", len(observation.tables))
        for element in observation.interactive_elements:
            print(
                f"  {element.semantic_role.value:14s} name={element.accessible_name!r:28s} "
                f"tag={element.tag:8s} enabled={element.enabled} vis={element.visible}"
            )
        for form in observation.forms:
            print("form:", form.form_id, [f"{f.role.value}:{f.label}" for f in form.fields])
        # Validation behaviour: submit with a bad email and re-observe.
        await page.evaluate("document.getElementById('em').value='nope'")
        await page.evaluate("document.getElementById('cust').requestSubmit()")
        after = await observer.observe(page)
        print("alerts after invalid submit:", json.dumps(after.alerts))
        print("browser version:", manager.browser_version)
        return 0
    finally:
        await manager.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
