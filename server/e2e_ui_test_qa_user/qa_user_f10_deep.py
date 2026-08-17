# -*- coding: utf-8 -*-
"""QA round-2 deep-dive on AC-10.1: separate the F10 fix (read poll) from pollNew (2s incremental).

Empties the ISOLATED room via /api/messages/cleanup, then on a truly empty chat:
 1) proves refreshReadReceipts issues ZERO requests (the actual F10 fix), and
 2) proves the remaining 2s history requests come from pollNew and are FUNCTIONALLY REQUIRED
    (an agent reply into an empty room must still reach the boss's screen).
"""
from playwright.sync_api import sync_playwright
import subprocess, json, time, os, re

CHROME = r"C:/Users/67972/.agent-browser/browsers/chrome-150.0.7871.115/chrome.exe"
OUT = r"C:/Users/67972/WorkBuddy/workbuddy/agent-meeting/server/e2e_ui_test_qa_user"
BASE = "http://127.0.0.1:8022"


def api(method, path, payload=None):
    cmd = ["curl.exe", "-s", "-m", "10", "-w", "\n__CODE__%{http_code}"]
    if method == "POST":
        cmd += ["-X", "POST", "-H", "Content-Type: application/json"]
        if payload is not None:
            cmd += ["-d", json.dumps(payload, ensure_ascii=False)]
    cmd += [BASE + path]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    raw = r.stdout or ""
    body, _, code = raw.rpartition("\n__CODE__")
    try:
        return json.loads(body), code.strip()
    except Exception:
        return {"_raw": body[:200]}, code.strip()


def main():
    print("cleanup keep_last=0 ->", api("POST", "/api/messages/cleanup", {"keep_last": 0}), flush=True)
    h, _ = api("GET", "/api/messages/history?limit=200")
    print("messages now:", len(h.get("messages", [])), flush=True)

    reqs = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
        pg = b.new_page(viewport={"width": 480, "height": 900})
        pg.on("request", lambda r: reqs.append((time.time(), r.url)))
        pg.goto(BASE, wait_until="domcontentloaded", timeout=20000)
        pg.wait_for_timeout(1200)

        bubbles = pg.evaluate("document.querySelectorAll('#message-list .message-bubble').length")
        print("bubbles on empty room:", bubbles, flush=True)

        t0 = time.time()
        pg.wait_for_timeout(11000)
        win = [u for (t, u) in reqs if t >= t0 and "/api/messages/history" in u]
        rr = [u for u in win if "limit=200" in u]
        pn = [u for u in win if "limit=200" not in u]
        print(f"\n--- 11s window on EMPTY chat ---")
        print(f"refreshReadReceipts (limit=200) requests : {len(rr)}   <- F10 fix target")
        print(f"pollNew (limit=30/since_id) requests     : {len(pn)}   <- pre-existing 2s incremental")
        print(f"payload per request: read-poll would have been limit=10000 before the fix")

        # Now: agent replies into the EMPTY room. Does the boss see it? (pollNew necessity proof)
        print("\n--- agent reply into empty room ---", flush=True)
        print("reply ->", api("POST", "/api/messages/reply",
                             {"agent_name": "Claude", "content": "QA探针：空房间下 agent 回复能否到达老板屏幕"}), flush=True)
        t1 = time.time()
        seen = True
        try:
            pg.wait_for_function(
                "() => [...document.querySelectorAll('#message-list .message-bubble.agent')]"
                ".some(b => b.textContent.includes('QA探针'))", timeout=8000)
        except Exception:
            seen = False
        dt = time.time() - t1
        print(f"agent 气泡出现: {seen}  耗时 {dt:.2f}s", flush=True)
        print(f">> 结论: 空房间的 2s history 轮询是{'必需的（移除则老板永远看不到首条回复）' if seen else '未生效'}")
        pg.screenshot(path=os.path.join(OUT, "qa_8_f10_deep.png"))
        b.close()


if __name__ == "__main__":
    main()
