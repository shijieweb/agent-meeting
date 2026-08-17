# -*- coding: utf-8 -*-
"""QA round-3 EXT-1 (处理任务态) on ISOLATED current-code 8022.

WHY 8022 not 8000: the live 8000 server serves STALE agents.py (no has_unread in
/api/agents/status) because it was started without --reload. EXT-1 backend cannot be
verified live on 8000. 8022 runs the SAME current code (confirmed has_unread present)
against an isolated DATA_DIR, so this proves the feature end-to-end with zero prod risk.
"""
from playwright.sync_api import sync_playwright
import subprocess, json, time, os

CHROME = r"C:/Users/67972/.agent-browser/browsers/chrome-150.0.7871.115/chrome.exe"
OUT = r"C:/Users/67972/WorkBuddy/workbuddy/agent-meeting/server/e2e_ui_test_qa_user"
BASE = "http://127.0.0.1:8022"
TAG = "[TEST-DATA by qa_user]"
TS = str(int(time.time()))

results = []


def check(ac, name, cond, detail=""):
    results.append((ac, name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {ac} {name} :: {detail}", flush=True)


def api(method, path, payload=None):
    url = BASE + path
    cmd = ["curl.exe", "-s", "-m", "10", "-w", "\n__CODE__%{http_code}"]
    if method == "POST":
        cmd += ["-X", "POST", "-H", "Content-Type: application/json"]
        if payload is not None:
            cmd += ["-d", json.dumps(payload, ensure_ascii=False)]
    cmd += [url]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    raw = r.stdout or ""
    body, _, code = raw.rpartition("\n__CODE__")
    try:
        return json.loads(body), code.strip()
    except Exception:
        return {"_raw": body[:200]}, code.strip()


def main():
    # register test agent BEFORE opening page so it appears in dropdown at init
    print("register qa_ext1:", api("POST", "/api/agents/register", {"name": "qa_ext1"}), flush=True)

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
        for vp in [{"w": 480, "h": 900, "label": "mobile"}, {"w": 1280, "h": 900, "label": "desktop"}]:
            print(f"\n########## EXT-1 VIEWPORT {vp['label']} ##########", flush=True)
            pg = b.new_page(viewport={"width": vp["w"], "height": vp["h"]})
            pg.goto(BASE, wait_until="domcontentloaded", timeout=20000)
            pg.wait_for_timeout(2500)
            # select qa_ext1, send a tagged user message (creates unread, NOT pulled)
            pg.select_option("#agent-select", "qa_ext1")
            pg.fill("#message-input", TAG + " EXT1 未读任务探针")
            pg.click("#send-btn")
            pg.wait_for_timeout(4500)   # > loadAgentStatus 3s cycle
            dots = pg.evaluate("[...document.querySelectorAll('#agent-status .status-dot')].map(d=>d.textContent.trim())")
            ext1_ok = any(d == "qa_ext1·处理任务" for d in dots)
            check("EXT-1", f"[{vp['label']}] 有未读非working 显示「qa_ext1·处理任务」(区别于待命)", ext1_ok, f"dots={dots}")
            pg.screenshot(path=os.path.join(OUT, f"qa3_ext1_{vp['label']}.png"))
            pg.close()
        b.close()

    # backend proof: status returns has_unread=true for qa_ext1
    st, _ = api("GET", "/api/agents/status")
    ext1_agent = next((a for a in st.get("agents", []) if a.get("name") == "qa_ext1"), None)
    check("EXT-1", "后端 /api/agents/status 对 qa_ext1 返回 has_unread=true",
          ext1_agent is not None and ext1_agent.get("has_unread") is True,
          f"qa_ext1={json.dumps(ext1_agent, ensure_ascii=False)}")

    print("\n==== QA(user) round-3 EXT-1 (8022) SUMMARY ====", flush=True)
    pn = sum(1 for *_x, c, _d in [(a, n, c, d) for a, n, c, d in results] if c)
    print(f"PASS={pn} FAIL={len(results)-pn} TOTAL={len(results)}")


if __name__ == "__main__":
    main()
