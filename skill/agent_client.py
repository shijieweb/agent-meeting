# -*- coding: utf-8 -*-
# ⚠️ DEPRECATED：本文件为早期实验性备用客户端，接口与 loop.py 不统一，
# 且缺少本地名字缓存 / UTF-8 保证 / 连接容错。新用户请一律使用 loop.py，
# 本文件仅保留作历史参考，不再维护。
"""Agent Hub 连接器（纯标准库，WorkBuddy 作大脑的「手」）。

设计原则（skill 方式）：
  Agent Hub 只做传输（register/pull/reply/已读回执/持久化）；
  思考由接入的 AI 工具（WorkBuddy）完成。本脚本是「手」，不内置任何回答逻辑。

子命令：
  register --name X
  list
  pull     --name X
  reply    --name X --msg-id Y --content "..." [--content-file path] [--client-msg-id C]
  send     --content "..." --target single|all [--target-agent Z]

注：本连接器只做「手」（收发/已读/持久化）。思考由本人（WorkBuddy）在对话里完成，
不内置任何自动 LLM 大脑。auto/MiniMax 模式已于 2026-08-16 应老板要求移除。
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error

SERVER = os.environ.get("AGENT_HUB_URL", "http://localhost:8000")


def _req(method, path, body=None, timeout=15):
    url = SERVER + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")
        try:
            detail = json.loads(detail).get("detail", detail)
        except Exception:
            pass
        print(f"[ERR] HTTP {e.code}: {detail}", file=sys.stderr)
        raise
    except urllib.error.URLError as e:
        print(f"[ERR] 连接失败 {url}: {e}", file=sys.stderr)
        raise


# ---------- 子命令实现 ----------
def cmd_register(args):
    return _req("POST", "/api/agents/register", {"name": args.name})


def cmd_list(_):
    return _req("GET", "/api/agents")


def cmd_pull(args):
    return {"messages": _req("GET", f"/api/messages/pull?agent_name={args.name}").get("messages", [])}


def cmd_reply(args):
    content = args.content
    if args.content_file:
        with open(args.content_file, "r", encoding="utf-8") as f:
            content = f.read().strip()
    if not content:
        print("[ERR] content 为空", file=sys.stderr)
        return None
    body = {
        "agent_name": args.name,
        "content": content,
        "reply_to_message_id": args.msg_id,
    }
    if args.client_msg_id:
        body["client_msg_id"] = args.client_msg_id
    return _req("POST", "/api/messages/reply", body)


def cmd_send(args):
    body = {
        "content": args.content,
        "target_type": args.target,
        "target_agent_name": args.target_agent,
    }
    return _req("POST", "/api/messages/send", body)


# ---------- 注：auto / MiniMax / OpenAI 自动大脑已于 2026-08-16 应老板要求整体移除 ----------
# 本连接器只做「手」（收发/已读/持久化），思考由本人（WorkBuddy）在对话里完成。


def main():
    p = argparse.ArgumentParser(description="Agent Hub 连接器（WorkBuddy 作大脑）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("register"); sp.add_argument("--name", required=True); sp.set_defaults(func=cmd_register)
    sp = sub.add_parser("list"); sp.set_defaults(func=cmd_list)
    sp = sub.add_parser("pull"); sp.add_argument("--name", required=True); sp.set_defaults(func=cmd_pull)
    sp = sub.add_parser("reply")
    sp.add_argument("--name", required=True); sp.add_argument("--msg-id", required=True)
    sp.add_argument("--content", default=None); sp.add_argument("--content-file", default=None)
    sp.add_argument("--client-msg-id", default=None); sp.set_defaults(func=cmd_reply)
    sp = sub.add_parser("send")
    sp.add_argument("--content", required=True); sp.add_argument("--target", default="single")
    sp.add_argument("--target-agent", default=None); sp.set_defaults(func=cmd_send)

    args = p.parse_args()
    try:
        result = args.func(args)
    except Exception as e:
        print(f"[FATAL] {e}", file=sys.stderr)
        sys.exit(1)
    if result is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
