# -*- coding: utf-8 -*-
"""通用 JSON 文件读写封装（全局锁 + 原子写，防并发损坏）。

对应方案书 §5.3 的"使用全局文件锁串行化对 reads.json 的读写"。
"""
import json
import os
import re
import tempfile
import threading
import time

_lock = threading.RLock()


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _path(name):
    from app.config import DATA_DIR
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, name)


def _read_nolock(name, default):
    """读取 JSON（调用方需已持有 _lock）；文件不存在或非法返回 default 并备份损坏文件。"""
    p = _path(name)
    if not os.path.isfile(p):
        return default
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, ValueError):
        backup = p + ".corrupt"
        try:
            os.replace(p, backup)
        except OSError:
            pass
        return default


def _write_nolock(name, data):
    """原子写（调用方需已持有 _lock）：写临时文件后 os.replace，避免半截文件。"""
    p = _path(name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    d = os.path.dirname(p)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def read_json(name, default):
    """读取 JSON；文件不存在或非法返回 default 并备份损坏文件。"""
    with _lock:
        return _read_nolock(name, default)


def write_json(name, data):
    """原子写：写临时文件后 os.replace，避免半截文件。"""
    with _lock:
        _write_nolock(name, data)


def update_json_atomic(name, default, mutator):
    """在锁内完成「读 -> mutator 修改 -> 写回」，保证 read-modify-write 原子。

    满足方案书 §5.3 / §7 T-PULL-05（并发拉取只一个拿到消息）。
    mutator(data) 直接原地修改 data（或将新数据 return），返回任意值会作为本函数结果。
    """
    with _lock:
        data = _read_nolock(name, default)
        result = mutator(data)
        _write_nolock(name, data)
        return result


# ---------------------------------------------------------------------------
# per-agent 已读集合（未读下沉：判断逻辑服务端做，客户端只透传）
# 落盘 data/agent_read_<X>.json，内容为已读消息 id 列表。
# ---------------------------------------------------------------------------

def agent_read_set_file(agent_name: str) -> str:
    """返回某 agent 的已读集合落盘文件名：data/agent_read_<X>.json。

    X 为对文件名非法/空白字符做了安全转义的 agent 名（中文名保留，仅替换 \\/:*?\"<>| 与空白）。
    """
    safe = re.sub(r'[\\/:*?"<>|\s]+', '_', str(agent_name))
    return "agent_read_{0}.json".format(safe)


def load_agent_read_set(agent_name: str) -> set:
    """读取某 agent 已读消息 id 集合；文件不存在或非法返回空集。"""
    return set(read_json(agent_read_set_file(agent_name), []))


def agent_read_set_exists(agent_name: str) -> bool:
    """该 agent 的已读集合文件是否已落盘（用于判断是否需要从 reads.json 迁移种子）。"""
    return os.path.isfile(_path(agent_read_set_file(agent_name)))


def save_agent_read_set(agent_name: str, read_set) -> None:
    """原子写某 agent 的已读消息 id 集合（迁移种子用）。"""
    write_json(agent_read_set_file(agent_name), sorted(set(read_set)))


def delete_agent_read_set(agent_name: str) -> bool:
    """删除某 agent 的已读集合文件（幽灵清理/手动 prune 时调用）。

    持 `_lock` 执行 os.remove；文件不存在返回 False（幂等，容忍并发/在途重建）。
    """
    with _lock:
        p = _path(agent_read_set_file(agent_name))
        try:
            if os.path.isfile(p):
                os.remove(p)
                return True
        except OSError:
            pass
        return False


def mark_agent_read(agent_name: str, message_ids) -> None:
    """在锁内把若干消息 id 加入该 agent 的已读集合（read-modify-write 原子）。

    用于服务端 pull 后持久化已读，保证后续 pull 不重复返回（去重下沉）。
    """
    ids = list(message_ids or [])
    if not ids:
        return

    def _mut(local_set):
        s = set(local_set)
        s.update(ids)
        return sorted(s)

    update_json_atomic(agent_read_set_file(agent_name), [], _mut)
