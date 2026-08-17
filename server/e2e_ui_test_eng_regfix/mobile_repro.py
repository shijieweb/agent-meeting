# -*- coding: utf-8 -*-
"""移动端白屏复现 / 验证脚本（回归B 根因定位）。

场景：
  A  baseline   : 线上当前 index.html + 当前 app.js（390x844 iPhone 视口）
  B  stalecache : 线上当前 index.html + **旧版 app.js**（模拟手机缓存了 20a728a 之前的 JS）
                  预期：旧 loadAgents() 无 null 守卫 -> TypeError -> init() 中断
                        -> loadInitialPage() 不执行 -> 消息列表为空 = 白屏

用法：
  python mobile_repro.py            # 跑全部场景
  python mobile_repro.py baseline   # 只跑指定场景
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

from playwright.sync_api import sync_playwright

BASE_URL: str = "http://localhost:8000"
HERE: str = os.path.dirname(os.path.abspath(__file__))
OLD_APP_JS: str = os.path.join(HERE, "old_app_d0df7fa.js")
SHOT_DIR: str = os.path.join(HERE, "shots")

# iPhone 12/13/14 逻辑分辨率
VIEWPORT: Dict[str, int] = {"width": 390, "height": 844}
UA: str = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

# 页面内探针：读取渲染实况（消息条数、关键元素可见性、IME 相关计算样式）
PROBE_JS: str = r"""
() => {
  const q = (s) => document.querySelector(s);
  const rect = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {x: Math.round(r.x), y: Math.round(r.y),
            w: Math.round(r.width), h: Math.round(r.height)};
  };
  const list = q('#message-list');
  const rows = list ? list.querySelectorAll('.msg-row') : [];
  const cs = (el, p) => el ? getComputedStyle(el).getPropertyValue(p).trim() : null;
  const sel = q('#agent-select');
  return {
    readyState: document.readyState,
    msgRowCount: rows.length,
    listChildCount: list ? list.children.length : -1,
    listRect: rect(list),
    containerRect: rect(q('.chat-container')),
    inputAreaRect: rect(q('.input-area')),
    headerRect: rect(q('.chat-header')),
    firstRowRect: rows.length ? rect(rows[0]) : null,
    lastRowRect: rows.length ? rect(rows[rows.length - 1]) : null,
    // 回归A：选人下拉框
    agentSelect: sel ? {
      rect: rect(sel),
      optionCount: sel.options.length,
      options: Array.from(sel.options).map(o => o.value),
      value: sel.value,
    } : null,
    // 回归：顶部状态栏必须保持已删除
    agentStatusExists: !!q('#agent-status'),
    statusDotCount: document.querySelectorAll('.status-dot').length,
    // IME 修复守卫（不得被回归）
    ime: {
      inputAreaFlexShrink: cs(q('.input-area'), 'flex-shrink'),
      listMinHeight: cs(list, 'min-height'),
      inputFontSize: cs(q('#message-input'), 'font-size'),
      containerHeight: cs(q('.chat-container'), 'height'),
    },
    // 静态资源引用（看是否带版本号）
    assets: {
      css: Array.from(document.querySelectorAll('link[rel=stylesheet]')).map(l => l.getAttribute('href')),
      js: Array.from(document.querySelectorAll('script[src]')).map(s => s.getAttribute('src')),
    },
  };
}
"""


def run_scenario(name: str, serve_old_js: bool) -> Dict[str, Any]:
    """在移动视口加载页面并采集渲染实况。

    Args:
        name: 场景名，用于截图命名。
        serve_old_js: True 时拦截 /static/app.js 返回旧版内容（模拟陈旧缓存）。

    Returns:
        采集结果字典，含 console/pageerror 与 DOM 探针数据。
    """
    console_errors: List[str] = []
    page_errors: List[str] = []
    console_all: List[str] = []

    old_js: str = ""
    if serve_old_js:
        with open(OLD_APP_JS, "r", encoding="utf-8") as f:
            old_js = f.read()

    os.makedirs(SHOT_DIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        context = browser.new_context(
            viewport=VIEWPORT,
            user_agent=UA,
            device_scale_factor=3,
            is_mobile=True,
            has_touch=True,
        )
        page = context.new_page()

        page.on("console", lambda m: (
            console_all.append(f"[{m.type}] {m.text}"),
            console_errors.append(m.text) if m.type == "error" else None,
        ))
        page.on("pageerror", lambda e: page_errors.append(str(e)))

        if serve_old_js:
            # 关键：只替换 app.js，index.html/css 仍取线上最新 —— 精确复刻「JS 旧、HTML 新」的缓存偏斜
            page.route(
                "**/static/app.js*",
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/javascript; charset=utf-8",
                    body=old_js,
                ),
            )

        page.goto(BASE_URL, wait_until="networkidle")
        page.wait_for_timeout(1500)  # 等首屏 fetch + 渲染落地

        probe: Dict[str, Any] = page.evaluate(PROBE_JS)
        shot = os.path.join(SHOT_DIR, f"mobile_{name}.png")
        page.screenshot(path=shot, full_page=False)

        context.close()
        browser.close()

    probe["_scenario"] = name
    probe["_screenshot"] = shot
    probe["_pageErrors"] = page_errors
    probe["_consoleErrors"] = console_errors
    probe["_consoleAll"] = console_all[-12:]
    return probe


def verdict(r: Dict[str, Any]) -> str:
    """依据消息条数判定是否白屏。"""
    if r["msgRowCount"] > 0:
        return "RENDER_OK"
    return "WHITE_SCREEN"


def main() -> None:
    wanted = sys.argv[1:] or ["baseline", "stalecache"]
    scenarios = {"baseline": False, "stalecache": True}
    results: List[Dict[str, Any]] = []

    for name in wanted:
        if name not in scenarios:
            print(f"!! unknown scenario: {name}")
            continue
        r = run_scenario(name, scenarios[name])
        results.append(r)

        print("=" * 68)
        print(f"SCENARIO {name}  ->  {verdict(r)}")
        print("=" * 68)
        print(f"  msg .msg-row count : {r['msgRowCount']}")
        print(f"  #message-list rect : {r['listRect']}")
        print(f"  .chat-container    : {r['containerRect']}")
        print(f"  .input-area rect   : {r['inputAreaRect']}")
        print(f"  first row rect     : {r['firstRowRect']}")
        print(f"  last  row rect     : {r['lastRowRect']}")
        print(f"  #agent-select      : {r['agentSelect']}")
        print(f"  #agent-status exist: {r['agentStatusExists']}  "
              f".status-dot: {r['statusDotCount']}")
        print(f"  IME guards         : {json.dumps(r['ime'], ensure_ascii=False)}")
        print(f"  assets             : {json.dumps(r['assets'], ensure_ascii=False)}")
        print(f"  pageerror          : {r['_pageErrors']}")
        print(f"  console errors     : {r['_consoleErrors']}")
        print(f"  screenshot         : {r['_screenshot']}")
        print()

    print("#" * 68)
    for r in results:
        print(f"  {r['_scenario']:<12} {verdict(r):<14} rows={r['msgRowCount']:<3} "
              f"select={'YES' if r['agentSelect'] else 'NO'}")
    print("#" * 68)


if __name__ == "__main__":
    main()
