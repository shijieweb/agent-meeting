from playwright.sync_api import sync_playwright
import os, sys

CHROME = r"C:/Users/67972/.agent-browser/browsers/chrome-150.0.7871.115/chrome.exe"
OUT = r"C:/Users/67972/WorkBuddy/workbuddy/agent-meeting/server/e2e_ui_test"

def main():
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--no-sandbox","--disable-gpu","--disable-dev-shm-usage"])
        pg = b.new_page(viewport={"width":480,"height":900})
        pg.goto("http://localhost:8000", wait_until="domcontentloaded", timeout=20000)
        pg.wait_for_timeout(3500)  # 等首屏 ?limit=30 + 初始轮询
        print("TITLE:", pg.title())
        bubbles = pg.evaluate("document.querySelectorAll('#message-list .message-bubble').length")
        print("BUBBLES:", bubbles)
        agents = pg.evaluate("[...document.querySelectorAll('#agent-select option')].map(o=>o.value)")
        print("AGENTS:", agents)
        statuses = pg.evaluate("document.querySelectorAll('#message-list .read-status').length")
        print("READ_STATUSES:", statuses)
        # 打印前几条消息文本片段 + 第一个 read-status 文本
        sample = pg.evaluate("""(() => {
            const items = [...document.querySelectorAll('#message-list .message-bubble')].slice(0,3).map(el=>el.className+':'+(el.textContent||'').slice(0,30));
            const rs = document.querySelector('#message-list .read-status');
            return {items, firstReadStatus: rs ? rs.textContent.trim() : null};
        })()""")
        print("SAMPLE:", sample)
        pg.screenshot(path=os.path.join(OUT,"shot_initial.png"))
        b.close()
        print("SMOKE_OK")

if __name__ == "__main__":
    main()
