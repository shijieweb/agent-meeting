# -*- coding: utf-8 -*-
"""QA round-4 supplement: D-2 fallback branch on LIVE 8000.

Injects a LOST agent whose name is empty -> reawaken hint must fall back to 'AI'
(and must NOT show hardcoded '阿编'). Zero prod mutation (request interception only).
"""
from playwright.sync_api import sync_playwright
import json, os, datetime

CHROME = r"C:/Users/67972/.agent-browser/browsers/chrome-150.0.7871.115/chrome.exe"
OUT = r"C:/Users/67972/WorkBuddy/workbuddy/agent-meeting/server/e2e_ui_test_qa_user"
BASE = "http://127.0.0.1:8000"


def now_minus(sec):
    return (datetime.datetime.now() - datetime.timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%S")


# lost (last_seen far past) but with EMPTY name -> fallback path
MOCK_LOST_NONAME = {"agents": [{"name": "", "last_seen": now_minus(900), "status": "working",
                                "session": True, "has_unread": False}]}

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, headless=True,
                          args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
    pg = b.new_page(viewport={"width": 480, "height": 900})
    pg.route("**/api/agents/status", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(MOCK_LOST_NONAME)))
    pg.goto(BASE, wait_until="domcontentloaded", timeout=20000)
    pg.wait_for_timeout(4500)
    hint = pg.evaluate("""(() => { const h=document.getElementById('reawaken-hint');
        const n=document.getElementById('lost-agent-name');
        return {disp:getComputedStyle(h).display, full:h.textContent.trim(), lost:n?n.textContent.trim():null}; })()""")
    ok = hint["lost"] == "AI" and "开会" in hint["full"] and "阿编" not in hint["full"]
    print(f"[{'PASS' if ok else 'FAIL'}] D-2 fallback 空名 -> 显示 'AI' 且无硬编码阿编 :: hint={hint}", flush=True)
    pg.screenshot(path=os.path.join(OUT, "qa4_d2_fallback_mobile.png"))
    pg.close()
    b.close()
