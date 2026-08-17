# -*- coding: utf-8 -*-
"""验证：移动端选人下拉框选择某个 agent 后发送，消息 target_type=single。"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List

import requests
from playwright.sync_api import sync_playwright

BASE_URL: str = "http://localhost:8000"
UA: str = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
SHOT_DIR: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")


def last_message() -> Dict:
    r = requests.get(f"{BASE_URL}/api/messages/history?limit=1", timeout=10)
    r.raise_for_status()
    msgs = r.json().get("messages", [])
    return msgs[0] if msgs else {}


def main() -> None:
    os.makedirs(SHOT_DIR, exist_ok=True)
    marker = f"[TEST-DATA by eng] {int(time.time())}"

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        context = browser.new_context(
            viewport={"width": 390, "height": 844},
            user_agent=UA,
            device_scale_factor=3,
            is_mobile=True,
            has_touch=True,
        )
        page = context.new_page()

        page.goto(BASE_URL, wait_until="networkidle")
        page.wait_for_timeout(800)

        # 选中 xiaobian
        page.locator("#agent-select").select_option("xiaobian")
        page.locator("#message-input").fill(marker)
        page.locator("#send-btn").click()

        # 等乐观渲染 + 后端落库
        page.wait_for_timeout(1200)
        page.screenshot(path=os.path.join(SHOT_DIR, "mobile_send_xiaobian.png"), full_page=False)

        context.close()
        browser.close()

    # 后端校验
    for _ in range(10):
        msg = last_message()
        if msg.get("content") == marker:
            break
        time.sleep(0.3)
    else:
        msg = {}

    print("=" * 60)
    print("SEND VERIFY RESULT")
    print("=" * 60)
    print(f"  marker text      : {marker}")
    print(f"  matched message  : {json.dumps(msg, ensure_ascii=False, indent=2)}")
    ok = (
        msg.get("content") == marker
        and msg.get("target_type") == "single"
        and msg.get("target_agent_name") == "xiaobian"
    )
    print(f"  assertion OK     : {ok}")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
