# -*- coding: utf-8 -*-
"""Agent Hub 连接器（本人=大脑，4 方法：init / pull / reply / end）。

仅做消息传输（register / pull / reply / 已读 / 持久化），所有思考由本人
（WorkBuddy 本对话实例 LLM）完成。未读去重由服务端保证（per-agent 已读集合），
本脚本纯透传，不再维护 seen.json。

方法（详见同目录 SKILL.md）：
  init  --name <名字>               上线初始化：写本地名 + 注册 + 置开会态(working)
  pull  [--interval 3 --max 1000]   收消息：轮询服务端未读，拉到即返回（watch 语义）
  reply --msg-id <id> --msg <文本>  发消息：直传文本（PYTHONUTF8=1 防命令行中文乱码）
  end                               收工：置离线(offline)

兼容别名：
  session --active --name <名字> == init
  session（不带 --active）        == end
  watch（可配 --interval/--max）  == pull
  register                        幂等注册（main 自动调用）

大脑恒为本人，绝无后台自循环、绝无 MiniMax/OpenAI 自动大脑。
"""
import argparse
import itertools
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# 保证 Python 以 UTF-8 处理命令行参数与标准输出（中文不乱码）。
os.environ.setdefault("PYTHONUTF8", "1")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover - 某些环境无 reconfigure
    pass
try:
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

SERVER = os.environ.get("AGENT_HUB_URL", "http://localhost:8000")
# 本地缓存目录（agent_name.txt / agent_read_*.json）。
# 默认指向内置 Agent Hub 实例；接入其它实例请用环境变量 AGENT_HUB_DATA_DIR 覆盖。
# 若默认路径不存在（非作者机器），自动回退到跨平台路径 ~/.agent_hub/data，开箱即用。
_BUILTIN_DATA_DIR = r"C:\Users\67972\WorkBuddy\workbuddy\会议系统\agent_hub\data"
DATA_DIR = os.environ.get("AGENT_HUB_DATA_DIR", _BUILTIN_DATA_DIR)
if not os.path.isdir(DATA_DIR):
    DATA_DIR = os.path.join(os.path.expanduser("~"), ".agent_hub", "data")
AGENT_NAME_FILE = os.path.join(DATA_DIR, "agent_name.txt")

# F7：reply 序号（进程内单调递增）+ 微秒时间戳。
# 注意：loop.py 以 CLI 每调用一进程，单纯 itertools.count 会每次从 1 重来，导致
# 同一 msg_id 两次 reply（两个进程）生成相同 client_msg_id 被服务端幂等拦截（AC-7.1 不通过）。
# 因此拼接微秒时间戳保证跨进程唯一；n 保留供同进程多次 do_reply 时递增可读。
# _req 在函数入口序列化 body 一次，内部网络重试复用同一 client_msg_id（单次 do_reply 幂等不双落）。
_reply_seq = itertools.count(1)


def _req(method, path, body=None, query=None, timeout=15, max_retries=3):
    """发起 HTTP 请求，返回解析后的 JSON。

    - body 为请求体字典（POST 用），自动序列化为 JSON；
    - query 为查询参数字典，自动做 URL 编码（agent 名含中文也不乱码）；
    - HTTP 业务错误：4xx 客户端错误直接抛出（参数不对，重试无用）；
      5xx 服务端错误与连接失败会按指数退避重试（默认 3 次：1s/2s/4s）；
    - 重试耗尽仍失败则抛出最后一次错误（ConnectionError 或 5xx HTTPError）。
    """
    url = SERVER + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    last_err = None
    for attempt in range(max_retries):
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            # 4xx 客户端错误：参数不对，重试无用，直接抛
            if e.code < 500:
                detail = e.read().decode("utf-8", "ignore")
                try:
                    detail = json.loads(detail).get("detail", detail)
                except Exception:
                    pass
                print("[ERR] HTTP {0}: {1}".format(e.code, detail), file=sys.stderr)
                raise
            # 5xx 服务端错误：可重试
            last_err = e
            print("[WARN] HTTP {0}（第 {1}/{2} 次，将退避重试）".format(
                e.code, attempt + 1, max_retries), file=sys.stderr)
        except urllib.error.URLError as e:
            last_err = ConnectionError("无法连接服务端 {0}: {1}".format(SERVER, e.reason))
            print("[WARN] 连接失败（第 {0}/{1} 次，将退避重试）".format(
                attempt + 1, max_retries), file=sys.stderr)
        # 指数退避（最后一次不睡）
        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)
    # 重试耗尽，抛出最后一次错误
    if last_err is not None:
        raise last_err
    raise ConnectionError("请求失败：重试耗尽且无可抛出错误")


def resolve_name(cli_name=None):
    """解析 agent 名字：优先 --name，其次本地 agent_name.txt，否则报错退出。"""
    if cli_name:
        return cli_name
    try:
        if os.path.isfile(AGENT_NAME_FILE):
            with open(AGENT_NAME_FILE, encoding="utf-8") as f:
                name = f.read().strip()
            if name:
                return name
    except Exception:
        pass
    print("请先 init（session --active --name <名字>）", file=sys.stderr)
    sys.exit(3)


def save_name(name):
    """把 agent 名字写入本地 data/agent_name.txt。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(AGENT_NAME_FILE, "w", encoding="utf-8") as f:
        f.write(name)


def load_name():
    """读取本地 agent 名字（无则返回空串）。"""
    try:
        if os.path.isfile(AGENT_NAME_FILE):
            with open(AGENT_NAME_FILE, encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass
    return ""


def ensure_registered(name):
    """幂等注册（register 由 main 自动调用；init/session/reply 也会确保在线）。"""
    try:
        _req("POST", "/api/agents/register", {"name": name})
    except Exception:
        pass


def do_init(name, active=True):
    """上线初始化：写本地名 + 注册 + 置开会态。"""
    save_name(name)
    ensure_registered(name)
    do_session(name, active=active)
    print("[ok] 已初始化并{0}: {1} @ {2}".format("上线" if active else "置离线", name, SERVER))


def do_session(name=None, active=True):
    """session 端点：active=True 开会(working)，active=False 收工(offline)。"""
    if name is None:
        name = resolve_name()
    ensure_registered(name)
    path = "/api/agents/{0}/session".format(urllib.parse.quote(name, safe=""))
    r = _req("POST", path, query={"active": "true" if active else "false"})
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return r


def do_end(name=None):
    """收工：置离线。"""
    if name is None:
        name = resolve_name()
    ensure_registered(name)
    path = "/api/agents/{0}/session".format(urllib.parse.quote(name, safe=""))
    r = _req("POST", path, query={"active": "false"})
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return r


def do_pull(name, interval=3.0, max_iters=1000):
    """收消息（watch 语义）：轮询服务端未读，拉到即返回；下次调用从 1 重新计数。

    未读去重由服务端保证（per-agent 已读集合），本方法纯透传、不本地去重。
    默认 interval=3s、max=1000 次；连接失败时持续重试（不退出）。
    """
    for _ in range(max_iters):
        msgs = []
        try:
            resp = _req("GET", "/api/messages/pull", query={"agent_name": name})
            msgs = resp.get("messages", [])
        except ConnectionError:
            msgs = []
        if msgs:
            print(json.dumps(msgs, ensure_ascii=False, indent=2))
            return msgs
        time.sleep(interval)
    print(json.dumps([], ensure_ascii=False))
    return []


def do_reply(name, msg_id, content=None, content_file=None, target_type=None, target_agent=None):
    """发消息：--msg 直传文本；--file 作长文本备选（utf-8，优先级高于 --msg）。

    F-c（连接器可用）：新增 --target-type / --target-agent，把目标透传给 /api/messages/reply，
    实现 Agent 互 @（回复某 Agent 或 @all 广播）。缺省不传 → 服务端兼容旧义（回复人类老板）。
    """
    if content_file:
        with open(content_file, encoding="utf-8") as f:
            content = f.read()
    if content is None:
        print("reply 需要 --msg 或 --file", file=sys.stderr)
        sys.exit(2)
    ensure_registered(name)
    n = next(_reply_seq)                            # F7：进程内序号（可读递增）
    client_msg_id = "c_{0}_{1}_{2}".format(msg_id or "", n, int(time.time() * 1000000))  # +微秒时间戳：跨进程唯一
    body = {
        "agent_name": name,
        "content": content,
        "reply_to_message_id": msg_id,
        "client_msg_id": client_msg_id,
    }
    # F-c：仅在显式给出时透传，不破旧调用（旧调用不传 → 服务端按回复人类老板处理）
    if target_type:
        body["target_type"] = target_type
    if target_agent:
        body["target_agent_name"] = target_agent
    r = _req("POST", "/api/messages/reply", body)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return r


def main():
    p = argparse.ArgumentParser(
        description="Agent Hub 连接器（本人=大脑，4 方法：init/pull/reply/end）"
    )
    p.add_argument(
        "cmd",
        nargs="?",
        default="pull",
        choices=["init", "pull", "watch", "reply", "register", "session", "end"],
    )
    p.add_argument("--version", action="version", version="agent-meeting 1.0.0")
    p.add_argument("--name", dest="name", help="Agent 名字（init/session 必填；其余默认读本地 agent_name.txt）")
    p.add_argument("--msg-id", dest="msg_id", help="reply 用：目标消息 id")
    p.add_argument("--msg", dest="msg", help="reply 用：直接传入文本（PYTHONUTF8=1 防命令行中文乱码）")
    p.add_argument("--file", dest="content_file", help="reply 用：长文本文件路径(utf-8)，优先级高于 --msg")
    p.add_argument("--target-type", dest="target_type", default=None,
                   help="reply 用（F-c 互@）：single|all（缺省回复人类老板=user）")
    p.add_argument("--target-agent", dest="target_agent", default=None,
                   help="reply 用（F-c 互@）：single 时的目标 Agent 名")
    p.add_argument("--interval", dest="interval", type=float, default=3.0,
                   help="pull/watch 轮询间隔秒数（默认3）")
    p.add_argument("--max", dest="max_iters", type=int, default=1000,
                   help="pull/watch 最大轮询次数（默认1000，约50分钟）")
    p.add_argument("--active", dest="active", action="store_true",
                   help="session 用：带=开会(置 online)，不带=收工(置 offline)")
    args = p.parse_args()

    if args.cmd == "init":
        if not args.name:
            print("init 需要 --name <名字>", file=sys.stderr)
            sys.exit(2)
        do_init(args.name, active=True)
        return

    if args.cmd == "session":
        if args.active:
            if not args.name:
                print("session --active 需要 --name <名字>", file=sys.stderr)
                sys.exit(2)
            do_init(args.name, active=True)
        else:
            do_end()
        return

    if args.cmd == "end":
        do_end(args.name)
        return

    if args.cmd == "register":
        name = resolve_name(args.name)
        ensure_registered(name)
        print("[ok] {0} 已注册/在线 @ {1}".format(name, SERVER))
        return

    if args.cmd == "pull" or args.cmd == "watch":
        name = resolve_name(args.name)
        do_pull(name, args.interval, args.max_iters)
        return

    if args.cmd == "reply":
        if not args.msg_id:
            print("reply 需要 --msg-id", file=sys.stderr)
            sys.exit(2)
        name = resolve_name(args.name)
        do_reply(name, args.msg_id, content=args.msg, content_file=args.content_file,
                 target_type=args.target_type, target_agent=args.target_agent)
        return


if __name__ == "__main__":
    main()
