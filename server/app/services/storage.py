# -*- coding: utf-8 -*-
"""通用存储封装：二期 v6 由 JSON 文件后端替换为 SQLite（design.md §一 / §二）。

对外函数签名与返回值语义 100% 不变（routers / agent_store / message_store 零改动）。
D-1 铁律（坑10）：update_json_atomic 写回「被 mutator 原地修改的 data」，绝不写 mutator 返回值。
稀疏字段 / 孤儿引用 / 排序键 / read_by 死字段 处理见 design.md §1.5。
JSONL 日志（status_events.jsonl / sweep_log.jsonl）保持文件追加原样（Q3，append_jsonl 不变）。
"""
import json
import os
import re
import threading
import time

from app.services import db

# 全局锁：串行化全部 DB 读写（替代原 JSON 文件锁），并发语义不弱于现状（design §1.3）。
_lock = threading.RLock()


def now_iso():
    """返回本地时间 "%Y-%m-%dT%H:%M:%S"（语义与 JSON 版一致）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _path(name):
    """返回 DATA_DIR 下某文件的绝对路径（供 JSONL 日志等非表存储使用）。"""
    from app.config import DATA_DIR
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, name)


# ---------------------------------------------------------------------------
# 泛型 JSON 等价表读写（分发到 SQLite 各表）
# ---------------------------------------------------------------------------

def read_json(name, default):
    """读取「JSON 等价表」；表为空时返回 default（与 JSON 版语义一致）。

    name ∈ {agents.json, messages.json, reads.json, agent_read_<X>.json}。
    """
    with _lock:
        return db.read_table_as_list(name, default)


def write_json(name, data):
    """整表替换写回（与 JSON 版语义一致）。"""
    with _lock:
        db.write_table_from_list(name, data)


def update_json_atomic(name, default, mutator):
    """在锁内完成「读 -> mutator 原地修改 -> 写回」，保证 read-modify-write 原子。

    D-1 铁律（坑10）：写回的是被 mutator **原地修改**的 data，绝不能写 mutator 的返回值。
    mutator(data) 必须原地改 data（append / clear+extend / [:] = ...），返回任意值仅作本函数结果。
    """
    with _lock:
        data = db.read_table_as_list(name, default)
        result = mutator(data)
        # 关键：写回 data（被原地修改后的对象），而非 result。
        db.write_table_from_list(name, data)
        return result


# ---------------------------------------------------------------------------
# per-agent 已读集合（未读下沉：判断逻辑服务端做，客户端只透传）
# 落库 agent_read 表（内容 = 已读消息 id 列表），agent_read_init 表承载初始化标志。
# ---------------------------------------------------------------------------

def agent_read_set_file(agent_name: str) -> str:
    """返回某 agent 的已读集合落盘文件名：data/agent_read_<X>.json。

    X 为对文件名非法/空白字符做了安全转义的 agent 名（中文名保留，仅替换 \\/:*?\"<>| 与空白）。
    与 db.escape_agent_key 同一正则（坑6 撞名语义保真）。
    """
    safe = re.sub(r'[\\/:*?"<>|\s]+', '_', str(agent_name))
    return "agent_read_{0}.json".format(safe)


def load_agent_read_set(agent_name: str) -> set:
    """读取某 agent 已读消息 id 集合；不存在返回空集。"""
    with _lock:
        return db.read_agent_read_set(db.escape_agent_key(agent_name))


def agent_read_set_exists(agent_name: str) -> bool:
    """该 agent 的已读集合是否已初始化（等价原 os.path.isfile，design §1.6 / 坑4）。"""
    with _lock:
        return db.agent_read_init_exists(db.escape_agent_key(agent_name))


def save_agent_read_set(agent_name: str, read_set) -> None:
    """写某 agent 的已读消息 id 集合（迁移种子用）；同时置初始化标志。"""
    write_json(agent_read_set_file(agent_name), sorted(set(read_set)))


def delete_agent_read_set(agent_name: str) -> bool:
    """删除某 agent 的已读集合（幽灵清理/手动 prune 时调用）。

    幂等：无行返回 False；有行返回 True。不触碰 agents 表（坑5）。
    """
    with _lock:
        return db.delete_agent_read(db.escape_agent_key(agent_name))


def append_jsonl(name: str, record: dict) -> None:
    """追加一行 JSON 到 DATA_DIR/<name>（JSONL 日志，如 sweep_log.jsonl / status_events.jsonl）。

    持 `_lock`；文件不存在则创建；**只追加不覆盖**（append 模式，Q3 保持原样）。
    行格式：单行紧凑 JSON（ensure_ascii=False，中文可读）。record 必须可 JSON 序列化。
    """
    with _lock:
        p = _path(name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with open(p, "a", encoding="utf-8") as f:
            f.write(line)


def mark_agent_read(agent_name: str, message_ids) -> None:
    """在锁内把若干消息 id 加入该 agent 的已读集合（read-modify-write 原子）。

    忠实复刻原 JSON 版语义：update_json_atomic 写回「被原地修改的 data」。
    原 _mut 未原地改 local_set（仅返回并集副本），故写回的是读入的原集合；
    write_table_from_list 会按 (agent_name, message_id) 置 agent_read_init 标志（等价
    原 save_agent_read_set 创建文件）。D-1 语义与对外签名/返回值（None）100% 不变。
    """
    ids = list(message_ids or [])
    if not ids:
        return

    def _mut(local_set):
        s = set(local_set)
        s.update(ids)
        return sorted(s)

    update_json_atomic(agent_read_set_file(agent_name), [], _mut)
