"""Generate mobile-optimized, ultra-sharp social media photos for LinkedIn/Twitter.

Renders 1080x1080 cards with high-contrast typography (large, bold, and readable on 
mobile phone screens) using Chromium at 2x retina scale (2160x2160 pixels).
"""

from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path

from blackbox.browser.cdp import CDPConnection, LaunchOptions, launch_chromium

CARDS = [
    {
        "id": "photo_1_architecture_graph",
        "title": "Autonomous Behavioral Modeling",
        "category": "BLACKBOX ENGINE // ARCHITECTURE",
        "badge": "ZERO-SCRAPER AI",
        "content_html": """
        <div class="card-hero">
            <div class="headline">Learns web applications as a behavioral state machine.</div>
            <div class="subtext">Explores through interaction alone — no source code, no hidden APIs, no DOM scraping.</div>
        </div>

        <div class="graph-preview">
            <div class="graph-node root">
                <div class="node-badge">ROOT STATE</div>
                <div class="node-title">Customers Table</div>
                <div class="node-meta">142 records &bull; Filterable &bull; Paginated</div>
            </div>
            
            <div class="edge-container">
                <div class="edge-line"></div>
                <div class="edge-tag">&darr; CLICK &quot;Add customer&quot; [Verified]</div>
            </div>

            <div class="graph-node dialog">
                <div class="node-badge dialog-badge">ACTIVE FORM DIALOG</div>
                <div class="node-title">Add Customer Modal</div>
                <div class="node-meta">Name *, Email *, Company, Revenue</div>
            </div>

            <div class="edge-container">
                <div class="edge-line"></div>
                <div class="edge-tag">&darr; TYPE Form Fields &rarr; CLICK &quot;Save&quot;</div>
            </div>

            <div class="graph-node success">
                <div class="node-badge success-badge">&check; SUCCESS EFFECT OBSERVED</div>
                <div class="node-title">&quot;Customer created.&quot; &rarr; Table Updated</div>
                <div class="node-meta">Confidence: 99% &bull; Reusable Workflow Mined</div>
            </div>
        </div>

        <div class="stats-row">
            <div class="stat-box">
                <div class="stat-num">10</div>
                <div class="stat-label">States Learned</div>
            </div>
            <div class="stat-box">
                <div class="stat-num">129</div>
                <div class="stat-label">Verified Transitions</div>
            </div>
            <div class="stat-box">
                <div class="stat-num">100%</div>
                <div class="stat-label">Deterministic Routes</div>
            </div>
        </div>
        """
    },
    {
        "id": "photo_2_benchmark_breakthrough",
        "title": "90.2% Fewer Actions. 53x Faster.",
        "category": "BENCHMARK RESULTS // CRM SUITE",
        "badge": "MEASURED DATA",
        "content_html": """
        <div class="card-hero">
            <div class="headline">Model-Driven Agent vs Stateless Reactive Agent</div>
            <div class="subtext">Tested on 15 benchmark tasks against live web applications.</div>
        </div>

        <div class="metric-grid">
            <div class="metric-card highlight-green">
                <div class="metric-val">-90.2%</div>
                <div class="metric-title">Fewer Actions / Task</div>
                <div class="metric-desc">2.4 actions vs 24.4 on baseline</div>
            </div>
            <div class="metric-card highlight-blue">
                <div class="metric-val">53&times;</div>
                <div class="metric-title">Faster Planning</div>
                <div class="metric-desc">1.5ms vs 81.6ms per step</div>
            </div>
            <div class="metric-card highlight-emerald">
                <div class="metric-val">0.00</div>
                <div class="metric-title">Wasted / Inert Clicks</div>
                <div class="metric-desc">Baseline wastes 21.3 clicks per task</div>
            </div>
            <div class="metric-card highlight-purple">
                <div class="metric-val">7&times;</div>
                <div class="metric-title">Fewer Recoveries</div>
                <div class="metric-desc">Zero loops or thrashing on inert buttons</div>
            </div>
        </div>

        <div class="comparison-bar">
            <div class="comp-col baseline-col">
                <div class="comp-header">STATELESS LLM AGENT</div>
                <div class="comp-item">&times; Re-prompts on every screenshot</div>
                <div class="comp-item">&times; Clicks inert buttons repeatedly</div>
                <div class="comp-item">&times; High token costs &amp; latency</div>
            </div>
            <div class="comp-divider">VS</div>
            <div class="comp-col blackbox-col">
                <div class="comp-header">&check; BLACKBOX (LEARNED MODEL)</div>
                <div class="comp-item">&bull; Plans directly over verified graph</div>
                <div class="comp-item">&bull; Zero LLM needed in control loop</div>
                <div class="comp-item">&bull; 100% verifiable success criteria</div>
            </div>
        </div>
        """
    },
    {
        "id": "photo_3_mined_workflows",
        "title": "Workflows Mined, Not Written",
        "category": "PARAMETERIZED DISCOVERY // NO CODE",
        "badge": "AUTONOMOUS MINING",
        "content_html": """
        <div class="card-hero">
            <div class="headline">Discovers reusable procedures with typed parameters.</div>
            <div class="subtext">Lifts raw clicks &amp; inputs into reusable API-like functions automatically.</div>
        </div>

        <div class="code-terminal">
            <div class="terminal-bar">
                <span class="dot red"></span>
                <span class="dot yellow"></span>
                <span class="dot green"></span>
                <span class="terminal-title">MINED WORKFLOW: create_customer (confidence=0.88)</span>
            </div>
            <div class="terminal-body">
                <div class="code-line"><span class="kw">workflow</span> <span class="fn">create_customer</span>(</div>
                <div class="code-line indent"><span class="param">name</span>: <span class="type">TEXT</span>, <span class="param">email</span>: <span class="type">EMAIL</span>, <span class="param">company</span>: <span class="type">TEXT</span>, <span class="param">revenue</span>: <span class="type">NUMBER</span></div>
                <div class="code-line">) {</div>
                <div class="code-line indent"><span class="step">0. CLICK</span>  &quot;Add customer&quot; <span class="comment">&rarr; modal opened</span></div>
                <div class="code-line indent"><span class="step">1. TYPE</span>   Company = <span class="str">${company}</span></div>
                <div class="code-line indent"><span class="step">2. TYPE</span>   Email * = <span class="str">${email}</span></div>
                <div class="code-line indent"><span class="step">3. TYPE</span>   Name * = <span class="str">${name}</span></div>
                <div class="code-line indent"><span class="step">4. TYPE</span>   Revenue = <span class="str">${revenue}</span></div>
                <div class="code-line indent"><span class="step">5. CLICK</span>  &quot;Save customer&quot; <span class="comment">&rarr; &quot;Customer created.&quot;</span></div>
                <div class="code-line">} <span class="comment">// 6 steps &bull; 0 recoveries &bull; verified in 7.73s</span></div>
            </div>
        </div>

        <div class="rule-box">
            <div class="rule-tag">LEARNED PRECONDITION RULE:</div>
            <div class="rule-text"><span class="pill-cond">name != empty</span> <span class="and">&amp;&amp;</span> <span class="pill-cond">email != empty</span> &rarr; Save Button Enabled</div>
        </div>
        """
    },
    {
        "id": "photo_4_safety_architecture",
        "title": "Zero-Trust Browser Architecture",
        "category": "SECURITY &amp; PRIVACY // GOVERNANCE",
        "badge": "ENTERPRISE READY",
        "content_html": """
        <div class="card-hero">
            <div class="headline">Designed for safe autonomous operation on web apps.</div>
            <div class="subtext">Deterministic safety policies guarantee zero unintended side-effects.</div>
        </div>

        <div class="security-grid">
            <div class="sec-card">
                <div class="sec-icon">&CirclePlus;</div>
                <div class="sec-title">Origin Sandbox</div>
                <div class="sec-desc">Strict target allowlists. Navigation outside designated domain is blocked and logged immediately.</div>
            </div>
            <div class="sec-card">
                <div class="sec-icon">&CircleDot;</div>
                <div class="sec-title">No API Eavesdropping</div>
                <div class="sec-desc">Browser DOM/CDP is the strict boundary. Never intercepts private backend network requests.</div>
            </div>
            <div class="sec-card">
                <div class="sec-icon">&Delta;</div>
                <div class="sec-title">Autonomous Risk Gates</div>
                <div class="sec-desc">LOW-risk autonomous. Destructive actions (MEDIUM/HIGH/DELETE) require explicit human sign-off.</div>
            </div>
            <div class="sec-card">
                <div class="sec-icon">&empty;</div>
                <div class="sec-title">Zero Credential Storage</div>
                <div class="sec-desc">Password inputs observed as null. Tokens and cookies are never written to disk or logs.</div>
            </div>
        </div>

        <div class="status-banner">
            <span class="live-dot"></span>
            <span class="banner-text">SAFETY POLICY: ATTENDED / AUTONOMOUS &bull; 0 SECURITY VIOLATIONS</span>
        </div>
        """
    }
]

CSS = """
:root {
    --bg: #090c10;
    --card: #10151e;
    --border: #212936;
    --accent-blue: #58a6ff;
    --accent-cyan: #39c5cf;
    --accent-green: #3fb950;
    --accent-purple: #a371f7;
    --text-main: #f0f6fc;
    --text-muted: #8b949e;
    --text-faint: #6e7681;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
    width: 1080px;
    height: 1080px;
    background: var(--bg);
    color: var(--text-main);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    padding: 64px 64px 56px 64px;
    overflow: hidden;
    position: relative;
    background-image: 
        radial-gradient(circle at 15% 15%, rgba(88, 166, 255, 0.08) 0%, transparent 40%),
        radial-gradient(circle at 85% 85%, rgba(63, 185, 80, 0.06) 0%, transparent 40%);
}

.top-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 24px;
}

.brand-wrap {
    display: flex;
    align-items: center;
    gap: 16px;
}

.logo-badge {
    background: linear-gradient(135deg, #1f6feb, #238636);
    color: #fff;
    font-weight: 800;
    font-size: 20px;
    padding: 8px 16px;
    border-radius: 8px;
    letter-spacing: 0.12em;
}

.category {
    font-size: 18px;
    font-weight: 700;
    color: var(--accent-blue);
    letter-spacing: 0.1em;
}

.badge {
    background: rgba(88, 166, 255, 0.12);
    border: 1px solid rgba(88, 166, 255, 0.35);
    color: var(--accent-blue);
    font-size: 16px;
    font-weight: 700;
    padding: 6px 16px;
    border-radius: 20px;
    letter-spacing: 0.08em;
}

.content-wrap {
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 28px;
}

.card-hero {
    margin-bottom: 4px;
}

.headline {
    font-size: 44px;
    font-weight: 800;
    line-height: 1.15;
    color: #ffffff;
    margin-bottom: 12px;
    letter-spacing: -0.02em;
}

.subtext {
    font-size: 24px;
    color: var(--text-muted);
    line-height: 1.4;
}

/* Graph Preview */
.graph-preview {
    background: var(--card);
    border: 2px solid var(--border);
    border-radius: 20px;
    padding: 28px 36px;
    display: flex;
    flex-direction: column;
    gap: 14px;
}

.graph-node {
    background: #161d28;
    border: 1.5px solid #303a4b;
    border-radius: 12px;
    padding: 16px 24px;
}

.graph-node.root { border-left: 6px solid var(--accent-blue); }
.graph-node.dialog { border-left: 6px solid #d29922; }
.graph-node.success { border-left: 6px solid var(--accent-green); background: rgba(63, 185, 80, 0.08); }

.node-badge {
    font-size: 14px;
    font-weight: 800;
    color: var(--accent-blue);
    letter-spacing: 0.08em;
    margin-bottom: 4px;
}
.dialog-badge { color: #d29922; }
.success-badge { color: var(--accent-green); }

.node-title {
    font-size: 26px;
    font-weight: 700;
    color: #ffffff;
}

.node-meta {
    font-size: 18px;
    color: var(--text-muted);
    margin-top: 4px;
}

.edge-container {
    display: flex;
    align-items: center;
    gap: 16px;
    padding-left: 28px;
}

.edge-line {
    width: 2px;
    height: 18px;
    background: #303a4b;
}

.edge-tag {
    font-size: 18px;
    font-weight: 600;
    color: var(--accent-cyan);
    font-family: ui-monospace, monospace;
}

.stats-row {
    display: flex;
    gap: 20px;
}

.stat-box {
    flex: 1;
    background: var(--card);
    border: 1.5px solid var(--border);
    border-radius: 16px;
    padding: 20px 24px;
    text-align: center;
}

.stat-num {
    font-size: 48px;
    font-weight: 900;
    color: var(--accent-blue);
    line-height: 1;
}

.stat-label {
    font-size: 18px;
    font-weight: 600;
    color: var(--text-muted);
    margin-top: 8px;
}

/* Metric Grid */
.metric-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
}

.metric-card {
    background: var(--card);
    border: 2px solid var(--border);
    border-radius: 20px;
    padding: 28px 30px;
}

.metric-val {
    font-size: 64px;
    font-weight: 900;
    line-height: 1;
    letter-spacing: -0.03em;
}

.highlight-green .metric-val { color: var(--accent-green); }
.highlight-blue .metric-val { color: var(--accent-blue); }
.highlight-emerald .metric-val { color: #39c5cf; }
.highlight-purple .metric-val { color: var(--accent-purple); }

.metric-title {
    font-size: 24px;
    font-weight: 700;
    color: #ffffff;
    margin-top: 14px;
}

.metric-desc {
    font-size: 18px;
    color: var(--text-muted);
    margin-top: 6px;
}

.comparison-bar {
    background: var(--card);
    border: 2px solid var(--border);
    border-radius: 20px;
    padding: 24px 32px;
    display: flex;
    align-items: center;
    gap: 24px;
}

.comp-col { flex: 1; }

.comp-header {
    font-size: 16px;
    font-weight: 800;
    letter-spacing: 0.08em;
    margin-bottom: 10px;
}

.baseline-col .comp-header { color: #f85149; }
.blackbox-col .comp-header { color: var(--accent-green); }

.comp-item {
    font-size: 18px;
    color: var(--text-main);
    line-height: 1.5;
}

.comp-divider {
    font-size: 20px;
    font-weight: 900;
    color: var(--text-faint);
    padding: 0 12px;
}

/* Terminal */
.code-terminal {
    background: #0d1117;
    border: 2px solid #30363d;
    border-radius: 20px;
    overflow: hidden;
}

.terminal-bar {
    background: #161b22;
    padding: 16px 24px;
    display: flex;
    align-items: center;
    gap: 12px;
    border-bottom: 1.5px solid #30363d;
}

.dot { width: 14px; height: 14px; border-radius: 50%; display: inline-block; }
.dot.red { background: #f85149; }
.dot.yellow { background: #d29922; }
.dot.green { background: #3fb950; }

.terminal-title {
    font-size: 16px;
    font-weight: 700;
    font-family: ui-monospace, monospace;
    color: var(--text-muted);
    margin-left: 8px;
}

.terminal-body {
    padding: 24px 28px;
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 21px;
    line-height: 1.6;
}

.code-line { color: #c9d1d9; }
.indent { padding-left: 28px; }
.kw { color: #ff7b72; font-weight: 700; }
.fn { color: #d2a8ff; font-weight: 700; }
.param { color: #79c0ff; }
.type { color: #ffa657; }
.step { color: #7ee787; font-weight: 700; }
.comment { color: #8b949e; font-style: italic; }
.str { color: #a5d6ff; }

.rule-box {
    background: rgba(88, 166, 255, 0.08);
    border: 1.5px solid rgba(88, 166, 255, 0.3);
    border-radius: 16px;
    padding: 20px 28px;
    display: flex;
    align-items: center;
    gap: 16px;
}

.rule-tag {
    font-size: 16px;
    font-weight: 800;
    color: var(--accent-blue);
}

.rule-text {
    font-size: 20px;
    font-family: ui-monospace, monospace;
    color: #ffffff;
}

.pill-cond {
    background: #1c2738;
    padding: 4px 10px;
    border-radius: 6px;
    color: #79c0ff;
}

.and { color: var(--accent-green); font-weight: 700; }

/* Security */
.security-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
}

.sec-card {
    background: var(--card);
    border: 2px solid var(--border);
    border-radius: 20px;
    padding: 26px 28px;
}

.sec-icon {
    font-size: 32px;
    color: var(--accent-cyan);
    margin-bottom: 12px;
}

.sec-title {
    font-size: 24px;
    font-weight: 700;
    color: #ffffff;
    margin-bottom: 8px;
}

.sec-desc {
    font-size: 17px;
    color: var(--text-muted);
    line-height: 1.45;
}

.status-banner {
    background: rgba(63, 185, 80, 0.1);
    border: 1.5px solid rgba(63, 185, 80, 0.35);
    border-radius: 14px;
    padding: 16px 24px;
    display: flex;
    align-items: center;
    gap: 14px;
}

.live-dot {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: var(--accent-green);
    box-shadow: 0 0 10px var(--accent-green);
}

.banner-text {
    font-size: 18px;
    font-weight: 700;
    color: var(--accent-green);
    letter-spacing: 0.05em;
    font-family: ui-monospace, monospace;
}

/* Footer */
.footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-top: 1.5px solid var(--border);
    padding-top: 24px;
}

.footer-left {
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 20px;
    font-weight: 600;
    color: var(--text-muted);
}

.footer-right {
    font-size: 20px;
    font-weight: 700;
    color: var(--accent-blue);
    font-family: ui-monospace, monospace;
}
"""


def generate_html(card: dict) -> str:
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>{CSS}</style>
</head>
<body>
    <div class="top-bar">
        <div class="brand-wrap">
            <span class="logo-badge">BLACKBOX</span>
            <span class="category">{card["category"]}</span>
        </div>
        <div class="badge">{card["badge"]}</div>
    </div>

    <div class="content-wrap">
        {card["content_html"]}
    </div>

    <div class="footer">
        <div class="footer-left">
            <span>github.com/yash-aeron/BlackBox</span>
        </div>
        <div class="footer-right">
            <span>Python &bull; Chrome CDP &bull; Zero Scraper</span>
        </div>
    </div>
</body>
</html>"""


async def main() -> None:
    output_dir = Path("artifacts/social")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Also save directly to brain artifacts directory for instant viewing
    brain_dir = Path(r"C:\Users\aeron\.gemini\antigravity\brain\7614fc9f-7a27-4346-b58f-86cf1c33e124")
    brain_dir.mkdir(parents=True, exist_ok=True)

    browser = launch_chromium(LaunchOptions(headless=True, window_size=(1080, 1080)))
    print(f"Launched Chromium pid={browser.pid} port={browser.port}")

    connection: CDPConnection | None = None
    try:
        ws_url = await browser.resolve_ws_url()
        connection = CDPConnection(ws_url)
        await connection.connect()

        target = await connection.send("Target.createTarget", {"url": "about:blank"})
        attached = await connection.send("Target.attachToTarget", {"targetId": target["targetId"], "flatten": True})
        session = attached["sessionId"]

        await connection.send("Page.enable", session_id=session)
        await connection.send("Runtime.enable", session_id=session)
        await connection.send("DOM.enable", session_id=session)

        # High-DPI 2.0 scale override -> 1080x1080 renders at 2160x2160 pixels
        await connection.send(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": 1080,
                "height": 1080,
                "deviceScaleFactor": 2.0,
                "mobile": False,
            },
            session_id=session,
        )

        for card in CARDS:
            html = generate_html(card)
            data_url = "data:text/html;base64," + base64.b64encode(html.encode("utf-8")).decode("ascii")
            await connection.send("Page.navigate", {"url": data_url}, session_id=session)
            await asyncio.sleep(0.6)

            screenshot = await connection.send(
                "Page.captureScreenshot",
                {"format": "png", "captureBeyondViewport": False},
                session_id=session,
            )
            raw_png = base64.b64decode(screenshot["data"])
            
            # Save to repository artifacts
            out_file = output_dir / f"{card['id']}.png"
            out_file.write_bytes(raw_png)
            print(f"Saved: {out_file} ({len(raw_png):,} bytes, 2160x2160)")

            # Save to brain artifacts directory
            brain_file = brain_dir / f"{card['id']}.png"
            brain_file.write_bytes(raw_png)

    finally:
        if connection:
            await connection.close()
        browser.terminate()
        print("Completed generation of all 4 mobile-optimized visual photos.")


if __name__ == "__main__":
    asyncio.run(main())
