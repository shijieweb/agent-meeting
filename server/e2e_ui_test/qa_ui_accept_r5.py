# -*- coding: utf-8 -*-
"""Round-5 independent visual/DOM acceptance (qa_ui) — commit 9f0e67f.
Isolated server http://127.0.0.1:8021, DATA_DIR=D:/tmp/am-qa-ui. Read-only on prod 8000.
Measures computed styles + real DOM via headless Chromium.
"""
from playwright.sync_api import sync_playwright
import json, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:8021"
SHOT = "D:/tmp/am-qa-ui/shot.png"
out = {}

def api(method, path, payload=None):
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return json.loads(r.read().decode()), r.status
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode()), e.code
        except Exception:
            return {"_err": e.reason}, e.code
    except Exception as e:
        return {"_err": str(e)}, 0

with sync_playwright() as p:
    b = p.chromium.launch(headless=True,
                          args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
    pg = b.new_page(viewport={"width": 400, "height": 800})
    console_errors, page_errors = [], []
    pg.on("console", lambda m: console_errors.append(f"{m.type}:{m.text}") if m.type == "error" else None)
    pg.on("pageerror", lambda e: page_errors.append(str(e)))
    # avoid benign favicon 404 console noise
    pg.route("**/favicon.ico", lambda route: route.fulfill(status=204, body=""))
    pg.goto(BASE, wait_until="domcontentloaded", timeout=20000)
    pg.wait_for_timeout(3500)
    pg.evaluate("fetch('/api/agents/status')")   # trigger a normal GET
    pg.wait_for_timeout(500)

    # A1 console zero errors
    out["A1_console_errors"] = console_errors
    out["A1_page_errors"] = page_errors
    out["A1_pass"] = (len(console_errors) == 0 and len(page_errors) == 0)

    # A2 reawaken-hint / lost-agent-name removed
    r1 = pg.evaluate("() => document.querySelector('#reawaken-hint')")
    r2 = pg.evaluate("() => document.querySelector('#lost-agent-name')")
    out["A2_reawaken_null"] = r1 is None
    out["A2_lostname_null"] = r2 is None
    out["A2_pass"] = (r1 is None and r2 is None)

    # A3 send-btn skin (width/height 40, radius 20px, svg with arrow path M14.536)
    sb = pg.evaluate("""() => {
        const e = document.getElementById('send-btn');
        const cs = getComputedStyle(e);
        const rect = e.getBoundingClientRect();
        const path = e.querySelector('path');
        return {
            w: Math.round(rect.width), h: Math.round(rect.height),
            borderRadius: cs.borderRadius,
            hasSvg: e.innerHTML.includes('<svg'),
            pathStart: path ? path.getAttribute('d') : null,
            text: e.textContent.trim()
        };
    }""")
    out["A3"] = sb
    out["A3_pass"] = (sb["w"] == 40 and sb["h"] == 40 and
                      ("20px" in sb["borderRadius"] or "50%" in sb["borderRadius"]) and
                      sb["hasSvg"] and (sb["pathStart"] or "").startswith("M14.536") and sb["text"] == "")

    # A4 header height 54-58
    hh = pg.evaluate("() => Math.round(document.querySelector('.chat-header').getBoundingClientRect().height)")
    out["A4_header_h"] = hh
    out["A4_pass"] = (54 <= hh <= 58)

    # seed messages via API (isolated server)
    api("POST", "/api/agents/register", {"name": "WorkBuddy"})
    api("POST", "/api/messages/send", {"sender_type": "user", "content": "QA视觉验收 user 消息", "target_type": "all"})
    api("POST", "/api/messages/reply", {"agent_name": "WorkBuddy", "content": "QA视觉验收 agent 消息"})
    pg.wait_for_timeout(4500)   # allow 2s pollNew to render both

    # A5 msg-row layout
    a5 = pg.evaluate("""() => {
        const rows = [...document.querySelectorAll('.msg-row')];
        const agentRow = rows.find(r => r.classList.contains('msg-in'));
        const userRow = rows.find(r => r.classList.contains('msg-out'));
        const aa = agentRow ? agentRow.querySelector('.msg-avatar') : null;
        const ua = userRow ? userRow.querySelector('.msg-avatar') : null;
        const ab = agentRow ? agentRow.querySelector('.msg-content > .msg-bubble') : null;
        return {
            rowCount: rows.length,
            hasAgentRow: !!agentRow,
            agentAvatarHasSvg: !!(aa && aa.innerHTML.includes('<svg')),
            agentHasContentBubble: !!ab,
            userAvatarText: ua ? ua.textContent.trim() : null
        };
    }""")
    out["A5"] = a5
    out["A5_pass"] = (a5["rowCount"] > 0 and a5["hasAgentRow"] and a5["agentAvatarHasSvg"]
                     and a5["agentHasContentBubble"] and a5["userAvatarText"] == "我")

    # A6 bubble colors
    a6 = pg.evaluate("""() => {
        const inB = document.querySelector('.msg-row.msg-in .msg-bubble');
        const outB = document.querySelector('.msg-row.msg-out .msg-bubble');
        return {
            inBg: inB ? getComputedStyle(inB).backgroundColor : null,
            outBg: outB ? getComputedStyle(outB).backgroundColor : null
        };
    }""")
    out["A6"] = a6
    out["A6_pass"] = (a6["inBg"] == "rgb(232, 244, 253)" and a6["outBg"] == "rgb(239, 253, 222)")

    # A7 100dvh container
    a7 = pg.evaluate("""() => {
        const c = document.querySelector('.chat-container');
        return { h: Math.round(c.getBoundingClientRect().height), ih: window.innerHeight };
    }""")
    out["A7"] = a7
    out["A7_pass"] = (abs(a7["h"] - a7["ih"]) <= 2)

    # A8 status dots: no 'lost' class, no 掉线/需重唤 text
    a8 = pg.evaluate("""() => {
        const dots = [...document.querySelectorAll('#agent-status .status-dot')];
        const texts = dots.map(d => d.textContent);
        return {
            count: dots.length,
            anyLost: dots.some(d => d.className.includes('lost')),
            anyDropText: texts.some(t => /掉线|需重唤/.test(t))
        };
    }""")
    out["A8"] = a8
    out["A8_pass"] = (not a8["anyLost"] and not a8["anyDropText"])

    pg.screenshot(path=SHOT)
    out["shot"] = SHOT
    b.close()

print(json.dumps(out, ensure_ascii=False, indent=2))
