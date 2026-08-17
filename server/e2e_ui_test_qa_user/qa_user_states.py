# -*- coding: utf-8 -*-
"""QA round-2 supplement: AC-9.2 four-state coverage (待命/已收工/离线/掉线) + hardcoded '阿编' probe.

Ages last_seen in the ISOLATED test DATA_DIR only (my own test data, no source file touched)
to reach the 离线(>120s, no session) and 掉线(>600s, session=true) branches without waiting.
"""
from playwright.sync_api import sync_playwright
import json, time, os, datetime

CHROME = r"C:/Users/67972/.agent-browser/browsers/chrome-150.0.7871.115/chrome.exe"
OUT = r"C:/Users/67972/WorkBuddy/workbuddy/agent-meeting/server/e2e_ui_test_qa_user"
DATA = r"C:/Users/67972/WorkBuddy/workbuddy/agent-meeting/server/test_data_qa_user"
BASE = "http://127.0.0.1:8022"


def iso_ago(sec):
    return (datetime.datetime.now() - datetime.timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%S")


def main():
    af = os.path.join(DATA, "agents.json")
    agents = json.load(open(af, encoding="utf-8"))
    for a in agents:
        if a["name"] == "Claude":            # session=true + 700s silent -> 掉线·需重唤
            a["last_seen"] = iso_ago(700); a["session"] = True; a["status"] = "working"
        if a["name"] == "WorkBuddy":         # session=false + 200s silent -> 离线
            a["last_seen"] = iso_ago(200); a["session"] = False; a["status"] = "waiting"
        if a["name"] == "NewAgent":          # fresh, never pulled -> 待命中
            a["last_seen"] = iso_ago(5); a["session"] = False; a["status"] = "waiting"
    json.dump(agents, open(af, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("aged isolated agents.json:", [(a["name"], a["last_seen"], a["status"], a["session"]) for a in agents], flush=True)

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
        pg = b.new_page(viewport={"width": 480, "height": 900})
        pg.goto(BASE, wait_until="domcontentloaded", timeout=20000)
        pg.wait_for_timeout(4500)

        dots = pg.evaluate("[...document.querySelectorAll('#agent-status .status-dot')].map(d=>d.textContent.trim())")
        hint = pg.evaluate("""(() => {
            const h=document.getElementById('reawaken-hint');
            return h ? {text:h.textContent.trim(), display:getComputedStyle(h).display, visible:h.offsetParent!==null} : null;
        })()""")
        print("\nDOTS   :", dots, flush=True)
        print("HINT   :", hint, flush=True)

        lost_ok = any("Claude·已掉线·需重唤" == d for d in dots)
        off_ok = any("WorkBuddy·离线" == d for d in dots)
        idle_ok = any("NewAgent·待命中" == d for d in dots)
        print(f"\n[{'PASS' if lost_ok else 'FAIL'}] AC-9.2 掉线态含真实名 'Claude·已掉线·需重唤' -> {lost_ok}")
        print(f"[{'PASS' if off_ok else 'FAIL'}] AC-9.2 离线态含真实名 'WorkBuddy·离线' -> {off_ok}")
        print(f"[{'PASS' if idle_ok else 'FAIL'}] AC-9.2 待命态含真实名 'NewAgent·待命中' -> {idle_ok}")

        # 硬编码 '阿编' 是否对用户可见（掉线时该 banner 会被 app.js 显示）
        leak = bool(hint and "阿编" in hint["text"] and hint["display"] != "none")
        print(f"\n[{'LEAK-FOUND' if leak else 'clean'}] 用户可见硬编码文案: display={hint['display'] if hint else None} "
              f"text={hint['text'] if hint else None}")
        print(f"  >> 实际掉线的是 Claude，但横幅写死『阿编』 -> 文案与真实 agent 不符: {leak and 'Claude' not in hint['text']}")

        pg.screenshot(path=os.path.join(OUT, "qa_7_states_and_hardcode.png"), full_page=True)
        b.close()


if __name__ == "__main__":
    main()
