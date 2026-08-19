# -*- coding: utf-8 -*-
"""文档协作系统·一期：documents / document_changes 专用 SQL CRUD（design v2.5 §五）。

R1 规避（design §一，工程师必读）
-------------------------------------------------
本模块**绝不**调用 storage.read_json / storage.write_json / storage.update_json_atomic
（它们最终走 db.write_table_from_list 的整表 DELETE+INSERT），而是用 db.get_conn() 直接做
「按主键的细粒度 SQL CRUD」（WHERE id=? / WHERE doc_id=?），并统一套 storage._lock 与全系统
串行化保持一致。documents / document_changes 是纯 SQLite 表，背后没有任何 JSON 文件，
永不进入 db._dispatch()，因此发一条聊天消息触发的 messages 整表重写
**在结构上不可能**冲掉文档元数据（R1 不成立）。

路径安全（v2.5 azhu #8 / AC-18）
-------------------------------------------------
DB 只存 file_uuid（服务端 uuid4）+ name（用户原名，仅用于展示与下载头）。磁盘路径恒由
    DATA_DIR/uploads/<file_uuid>_<safe(name)>
在代码内重建，**绝不读取/拼接任何 DB 存储的 path 字符串**；重建后再用 os.path.realpath
校验必须落在上传根目录之内，越界 → 403。file_uuid 亦强校验为 32 位 hex，被篡改（如塞
"../../etc/passwd"）→ 403，不可能读到受控目录之外的文件。

身份可信（AC-17/19/22）
-------------------------------------------------
本模块所有写操作都要求调用方传入 actor dict（由 routers/docs.derive_actor 服务端推导），
document_changes.actor 只取 actor["owner"]，绝不接受任何请求体字段。
"""
import os
import re
import sqlite3
import uuid

from fastapi import HTTPException

from app.config import (
    DATA_DIR,
    DOC_LIST_DEFAULT_LIMIT,
    DOC_LIST_MAX_LIMIT,
    EXTERNAL_BASE_URL,
    MAX_UPLOAD_SIZE,
    SUPER_ADMINS,
    UPLOAD_SUBDIR,
)
from app.services import db, message_store
from app.services.storage import _lock, now_iso

# ---------------------------------------------------------------------------
# 格式白名单（AI 友好格式，design §八）
# ---------------------------------------------------------------------------
# 以「扩展名」为权威判定依据（而非客户端可任意伪造的 Content-Type）：
# 扩展名不在表内 → 400。这样 .svg / .exe 天然被拒，且攻击者无法用
# `Content-Type: text/plain` 把 .svg 混进来（AC-2.4 / azhu #5）。
EXT_MIME = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".json": "application/json",
    ".csv": "text/csv",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# 纯文本类：支持在线编辑 + MD 渲染预览（R3 / AC-16 / AC-20）。
TEXT_MIMES = frozenset(["text/plain", "text/markdown", "application/json", "text/csv"])

# 明确排除：SVG 可内嵌脚本（XSS），一律拒收（azhu #5 / design §八）。
BLOCKED_MIMES = frozenset(["image/svg+xml"])

# 新增空文档默认扩展名（无扩展名时补 .md，便于 MD 渲染预览）。
DEFAULT_TEXT_EXT = ".md"

# file_uuid 强校验：uuid4().hex 恒为 32 位小写 hex。任何其它形态（含 ../ 遍历串）→ 403。
_UUID_RE = re.compile(r"^[0-9a-f]{32}$")

# 磁盘文件名安全化：路径分隔符 / 控制字符 / Windows 非法字符 一律折叠成 "_"。
_UNSAFE_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')

# 动作中文文案（summary / 群通知用）。
_ACTION_CN = {
    "upload": "上传",
    "overwrite": "覆盖更新",
    "create": "新建",
    "edit": "编辑",
    "rename": "改名",
    "delete": "删除",
}

_schema_ready = False


# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------

def _ensure_schema() -> None:
    """确保 documents / document_changes 两表存在（进程内只做一次）。

    建表唯一入口仍是 db.init_db()（design §七），此处只是惰性触发它，
    保证「服务先于 migrate.py 起来」或隔离测试新建 DATA_DIR 时不会 no such table。
    幂等（CREATE TABLE IF NOT EXISTS），不触碰既有 5 表数据。
    """
    global _schema_ready
    if not _schema_ready:
        db.init_db()
        _schema_ready = True


def gen_id(prefix: str = "doc") -> str:
    """生成短 id（与 message_store.gen_id 同风格）。"""
    return "{0}_{1}".format(prefix, uuid.uuid4().hex[:10])


def uploads_root() -> str:
    """上传根目录绝对路径（DATA_DIR/uploads），不存在则创建。"""
    root = os.path.join(DATA_DIR, UPLOAD_SUBDIR)
    os.makedirs(root, exist_ok=True)
    return os.path.realpath(root)


def relative_path(file_uuid: str, name: str) -> str:
    """返回 DB/日志展示用的**相对** path（如 uploads/<uuid>_<name>）。

    仅用于展示与「DB 只存相对路径」的语义表达；下载/读写一律走 resolve_disk_path()
    重建，绝不拼接本函数结果（AC-11 / AC-18）。
    """
    return "{0}/{1}".format(UPLOAD_SUBDIR, disk_name(file_uuid, name))


def safe_component(name: str) -> str:
    """把用户原名压成安全的单段文件名（去目录、去 .. 、去非法字符）。

    纯函数、确定性：同一 name 恒得同一结果，故「用 file_uuid + DB name 重建路径」可复现。
    """
    base = os.path.basename(str(name or "")).replace("..", "_")
    base = _UNSAFE_RE.sub("_", base).strip().strip(".")
    if not base:
        base = "unnamed"
    return base[:180]


def disk_name(file_uuid: str, name: str) -> str:
    """磁盘文件名 = <file_uuid>_<safe(原名)>（design §八）。"""
    return "{0}_{1}".format(file_uuid, safe_component(name))


def resolve_disk_path(doc: dict) -> str:
    """由 file_uuid + name 在代码内重建磁盘绝对路径，并做遍历越界校验（AC-18）。

    绝不读取/信任任何 DB 存储的 path 字符串。校验两道：
      ① file_uuid 必须是 32 位 hex（被篡改成 "../../etc/passwd" → 403）；
      ② realpath 后必须仍落在 uploads 根目录之内（否则 403）。
    """
    file_uuid = str(doc.get("file_uuid") or "")
    if not _UUID_RE.match(file_uuid):
        raise HTTPException(status_code=403, detail="invalid stored file reference")
    root = uploads_root()
    candidate = os.path.realpath(os.path.join(root, disk_name(file_uuid, doc.get("name"))))
    if candidate != root and not candidate.startswith(root + os.sep):
        raise HTTPException(status_code=403, detail="path escapes upload root")
    return candidate


def guess_mime(filename: str) -> str:
    """按扩展名判定 mime；不在白名单 → 400（含 .svg / .exe，AC-2.4）。"""
    ext = os.path.splitext(str(filename or ""))[1].lower()
    mime = EXT_MIME.get(ext)
    if not mime or mime in BLOCKED_MIMES:
        raise HTTPException(
            status_code=400,
            detail="unsupported format: {0}; allowed: {1}".format(
                ext or "(no ext)", ", ".join(sorted(set(EXT_MIME.values())))
            ),
        )
    return mime


def is_text_mime(mime: str) -> bool:
    """纯文本类（可在线编辑 + MD 预览）。"""
    return mime in TEXT_MIMES


def build_url(doc_id: str) -> str:
    """外网下载 URL：运行时由 EXTERNAL_BASE_URL 拼接（DB 不存完整 URL，AC-11）。"""
    return "{0}/api/docs/{1}/download".format(EXTERNAL_BASE_URL, doc_id)


def can_write(actor: dict, doc: dict) -> bool:
    """写权限判定（design §四；v2.5 azhu #12 owner_type + owner 双字段校验）。

    - 人类网页操作员（sender_type=="user"）恒为 super-admin：可改/删/覆盖任意文档
      （老板权限不受影响）；
    - owner 本人：须 owner **且** owner_type 双字段一致才算「自己」——即便某 Agent 名恰为
      "user"，其 owner_type='agent' 也与人类 owner_type='user' 区分，杜绝冒充/互踩；
    - SUPER_ADMINS（env 额外账号）：允许。
    """
    if actor.get("sender_type") == "user":
        return True
    if actor.get("owner") == doc.get("owner") and actor.get("owner_type") == doc.get("owner_type"):
        return True
    if actor.get("owner") in SUPER_ADMINS:
        return True
    return False


def derive_actor(body: dict, agent_name: str = None) -> dict:
    """身份可信推导（AC-17/19/22）。

    铁律：sender_type / owner / owner_type / document_changes.actor
    一律服务端按路由推导，绝不接受请求体传入。

    规则：
      - 有 agent_name + 白名单 → Agent（owner_type='agent'）
      - 无 agent_name → 人类网页操作员（owner_type='user'，恒 super-admin）
    """
    from app.config import HUMAN_OWNER
    from app.services.agent_store import agent_exists

    forbidden = [k for k in ("sender_type", "owner", "owner_type") if k in body]
    if forbidden:
        raise HTTPException(
            status_code=400,
            detail="sender_type/owner/owner_type are server-derived; do not send",
        )
    if agent_name:
        if not agent_exists(agent_name):
            raise HTTPException(status_code=403, detail="agent not in whitelist: " + agent_name)
        return {
            "sender_type": "agent",
            "owner": agent_name,
            "owner_type": "agent",
            "is_super": False,
        }
    # 人类网页操作员（8787 门户，F-2 网络边界背书）
    return {
        "sender_type": "user",
        "owner": HUMAN_OWNER,
        "owner_type": "user",
        "is_super": True,
    }


def download_doc(doc_id: str) -> dict:
    """获取文档元数据（不含 changes，供 docs 详情端点用）。"""
    doc = public_doc(get_doc(doc_id))
    return doc


def _txn(fn):
    """在显式事务内执行 fn(conn)（BEGIN IMMEDIATE / COMMIT，坑8）。

    注意：绝不可在本事务内调用 message_store.*（它会自开 BEGIN IMMEDIATE →
    "cannot start a transaction within a transaction"）。群通知一律在 COMMIT 之后发。
    """
    conn = db.get_conn()
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = fn(conn)
        conn.execute("COMMIT")
        return result
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise


def _row_to_doc(row) -> dict:
    """sqlite3.Row → 文档元数据 dict（含运行时拼接的 url + editable）。"""
    mime = row["mime"] or ""
    return {
        "id": row["id"],
        "name": row["name"],
        "file_uuid": row["file_uuid"],
        "owner": row["owner"],
        "owner_type": row["owner_type"],
        "mime": mime,
        "size": int(row["size"] or 0),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "url": build_url(row["id"]),
        "editable": is_text_mime(mime),
    }


def public_doc(doc: dict) -> dict:
    """对外输出的元数据：剥掉 file_uuid（磁盘细节不外泄，AC-18.2）。"""
    out = dict(doc)
    out.pop("file_uuid", None)
    return out


def _select_doc(conn, doc_id: str):
    return conn.execute(
        "SELECT id, name, file_uuid, owner, owner_type, mime, size, created_at, updated_at "
        "FROM documents WHERE id=?",
        (doc_id,),
    ).fetchone()


def _record_change(conn, doc_id: str, actor: dict, action: str, summary: str) -> None:
    """写一条改动记录。

    actor **强制**来自参数（derive_actor 的结果），绝不读任何请求体字段（azhu #9 / AC-19）。
    document_changes 表只有 5 列（design §三 3.2 固定），故把 owner_type 以 "[user]/[agent]"
    前缀编入 summary，保证审计可区分同名的人类/Agent。
    """
    conn.execute(
        "INSERT INTO document_changes (id, doc_id, actor, action, summary, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            gen_id("chg"),
            doc_id,
            actor["owner"],          # 服务端推导的真实调用方，不可伪造
            action,
            "[{0}] {1}".format(actor.get("owner_type", "user"), summary),
            now_iso(),
        ),
    )


def _notify(action: str, actor: dict, doc_id: str, name: str, with_link: bool = True) -> None:
    """发一条群 system 消息（气泡文件名可点击 = AC-4）。

    content 用 Markdown 链接 `[文件名](外网URL)`，前端 renderSystemContent 渲染成 <a>。
    失败不抛（通知是附属能力，绝不因群通知失败回滚已成功的文档操作）。
    """
    try:
        label = _ACTION_CN.get(action, action)
        if with_link:
            body = "[{0}]({1})".format(name, build_url(doc_id))
        else:
            body = name
        content = "{0} {1}了文档 {2}".format(actor["owner"], label, body)
        message_store.add_system_message(content, message_type="doc_event")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 读接口（GET/下载/列表：所有人可读，design §四）
# ---------------------------------------------------------------------------

def list_docs(owner=None, limit: int = DOC_LIST_DEFAULT_LIMIT, offset: int = 0) -> dict:
    """分页列表（azhu #11 / AC-21）：默认 limit=50、offset=0，按 updated_at desc，返回 total。"""
    if limit is None or limit <= 0:
        limit = DOC_LIST_DEFAULT_LIMIT
    limit = min(int(limit), DOC_LIST_MAX_LIMIT)
    offset = max(int(offset or 0), 0)
    with _lock:
        _ensure_schema()
        conn = db.get_conn()
        if owner:
            total = conn.execute(
                "SELECT count(*) FROM documents WHERE owner=?", (owner,)
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT id, name, file_uuid, owner, owner_type, mime, size, created_at, updated_at "
                "FROM documents WHERE owner=? ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
                (owner, limit, offset),
            ).fetchall()
        else:
            total = conn.execute("SELECT count(*) FROM documents").fetchone()[0]
            rows = conn.execute(
                "SELECT id, name, file_uuid, owner, owner_type, mime, size, created_at, updated_at "
                "FROM documents ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return {
            "docs": [public_doc(_row_to_doc(r)) for r in rows],
            "total": int(total),
            "limit": limit,
            "offset": offset,
        }


def get_doc(doc_id: str) -> dict:
    """取文档元数据（内部用，含 file_uuid）；不存在 → 404。"""
    with _lock:
        _ensure_schema()
        row = _select_doc(db.get_conn(), doc_id)
        if row is None:
            raise HTTPException(status_code=404, detail="document not found: " + str(doc_id))
        return _row_to_doc(row)


def list_changes(doc_id: str) -> list:
    """改动记录列表（who/when/action/summary），按时间升序。"""
    with _lock:
        _ensure_schema()
        rows = db.get_conn().execute(
            "SELECT id, doc_id, actor, action, summary, created_at FROM document_changes "
            "WHERE doc_id=? ORDER BY created_at, id",
            (doc_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "doc_id": r["doc_id"],
                "actor": r["actor"],
                "action": r["action"],
                "summary": r["summary"] or "",
                "created_at": r["created_at"],
            }
            for r in rows
        ]


def get_detail(doc_id: str) -> dict:
    """详情 = 元数据 + 外网 url + changes（AC-9.2）。"""
    doc = public_doc(get_doc(doc_id))
    doc["changes"] = list_changes(doc_id)
    return doc


def get_download(doc_id: str) -> dict:
    """下载所需信息：{path, name, mime}。

    路径由 file_uuid + name 在代码重建（resolve_disk_path），绝不拼 DB path 字符串；
    越界 → 403；磁盘文件缺失 → 404（AC-9.3 / AC-18）。
    """
    doc = get_doc(doc_id)
    path = resolve_disk_path(doc)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="file missing on disk")
    return {"path": path, "name": doc["name"], "mime": doc["mime"] or "application/octet-stream"}


def read_text(doc_id: str) -> str:
    """读纯文本正文（供在线编辑器回填）；二进制格式 → 400。"""
    doc = get_doc(doc_id)
    if not is_text_mime(doc["mime"]):
        raise HTTPException(status_code=400, detail="binary format is not editable online")
    path = resolve_disk_path(doc)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="file missing on disk")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


# ---------------------------------------------------------------------------
# 写接口（权限一律 can_write，actor 一律服务端推导）
# ---------------------------------------------------------------------------

def upload(file_bytes: bytes, filename: str, actor: dict, doc_id=None) -> dict:
    """上传（新建）或覆盖更新一份文档。

    校验：空文件→400；>5MB→413；扩展名不在白名单→400（含 SVG）。
    覆盖（doc_id 非空）：先 get_doc → can_write 收紧（Agent 仅可覆盖 owner==自身，azhu #12），
    否则 403。文件名与原名相同 → 复用 file_uuid 原地覆盖同一磁盘文件（mtime 更新）；
    文件名变化 → 生成新 file_uuid 落新文件并删除旧文件（design §12.4 步骤 2）。
    落库后（事务已 COMMIT）再发群 system 消息。
    """
    if not file_bytes:
        raise HTTPException(status_code=400, detail="empty file is not allowed")
    if len(file_bytes) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail="file too large: {0} > {1} bytes".format(len(file_bytes), MAX_UPLOAD_SIZE),
        )
    mime = guess_mime(filename)
    display_name = safe_component(filename)
    size = len(file_bytes)

    with _lock:
        _ensure_schema()
        conn = db.get_conn()
        ts = now_iso()

        if doc_id:
            # ---- 覆盖分支 ----
            row = _select_doc(conn, doc_id)
            if row is None:
                raise HTTPException(status_code=404, detail="document not found: " + str(doc_id))
            existing = _row_to_doc(row)
            if not can_write(actor, existing):
                raise HTTPException(
                    status_code=403,
                    detail="not allowed to overwrite doc owned by {0}({1})".format(
                        existing["owner"], existing["owner_type"]
                    ),
                )
            old_path = resolve_disk_path(existing)
            same_name = (display_name == existing["name"])
            new_uuid = existing["file_uuid"] if same_name else uuid.uuid4().hex
            new_doc = dict(existing)
            new_doc["file_uuid"] = new_uuid
            new_doc["name"] = display_name
            new_path = resolve_disk_path(new_doc)
            with open(new_path, "wb") as f:      # 落盘先于改库：库指向的文件恒存在
                f.write(file_bytes)
            summary = "{0} → {1} 字节".format(existing["size"], size)
            if not same_name:
                summary = "{0}（{1} → {2}）".format(summary, existing["name"], display_name)

            def _mut(c):
                c.execute(
                    "UPDATE documents SET name=?, file_uuid=?, mime=?, size=?, updated_at=? WHERE id=?",
                    (display_name, new_uuid, mime, size, ts, doc_id),
                )
                _record_change(c, doc_id, actor, "overwrite", "覆盖更新 {0}：{1}".format(display_name, summary))
                return None

            _txn(_mut)
            if old_path != new_path and os.path.isfile(old_path):
                try:
                    os.remove(old_path)          # 清理改名后的旧文件，避免磁盘孤儿
                except OSError:
                    pass
            row = _select_doc(conn, doc_id)
            result = public_doc(_row_to_doc(row))
            result["action"] = "overwrite"
        else:
            # ---- 新建分支 ----
            new_id = gen_id("doc")
            file_uuid = uuid.uuid4().hex          # 服务端生成，绝不与客户端输入拼接
            path = resolve_disk_path({"file_uuid": file_uuid, "name": display_name})
            with open(path, "wb") as f:
                f.write(file_bytes)

            def _mut(c):
                c.execute(
                    "INSERT INTO documents (id, name, file_uuid, owner, owner_type, mime, size, "
                    "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (new_id, display_name, file_uuid, actor["owner"], actor["owner_type"],
                     mime, size, ts, ts),
                )
                _record_change(c, new_id, actor, "upload",
                               "上传 {0}（{1} 字节）".format(display_name, size))
                return None

            _txn(_mut)
            row = _select_doc(conn, new_id)
            result = public_doc(_row_to_doc(row))
            result["action"] = "upload"

        _notify(result["action"], actor, result["id"], result["name"])
        return result


def create_empty(name: str, actor: dict, content: str = "") -> dict:
    """新建（空/带初稿）文本文档：仅网页端（Agent 由路由层拦 403，AC-12）。

    仅支持纯文本类扩展名；无扩展名自动补 .md（便于 MD 渲染预览）。
    """
    raw = safe_component(name)
    ext = os.path.splitext(raw)[1].lower()
    if not ext:
        raw = raw + DEFAULT_TEXT_EXT
    mime = guess_mime(raw)
    if not is_text_mime(mime):
        raise HTTPException(
            status_code=400,
            detail="create supports text formats only (txt/md/json/csv); use upload for binary",
        )
    data = (content or "").encode("utf-8")
    if len(data) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="content too large")

    with _lock:
        _ensure_schema()
        conn = db.get_conn()
        ts = now_iso()
        new_id = gen_id("doc")
        file_uuid = uuid.uuid4().hex
        path = resolve_disk_path({"file_uuid": file_uuid, "name": raw})
        with open(path, "wb") as f:
            f.write(data)

        def _mut(c):
            c.execute(
                "INSERT INTO documents (id, name, file_uuid, owner, owner_type, mime, size, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (new_id, raw, file_uuid, actor["owner"], actor["owner_type"],
                 mime, len(data), ts, ts),
            )
            _record_change(c, new_id, actor, "create",
                           "新建 {0}（{1} 字节）".format(raw, len(data)))
            return None

        _txn(_mut)
        result = public_doc(_row_to_doc(_select_doc(conn, new_id)))
        _notify("create", actor, new_id, raw)
        return result


def edit_doc(doc_id: str, actor: dict, content=None, name=None) -> dict:
    """PUT 编辑语义（azhu #10 / AC-20）。

    = 重写磁盘文件内容 + 同步 documents.name/updated_at + 记 change(edit/rename)。
    - 二进制格式（pdf/docx/png/jpg）→ 400，提示走覆盖上传（R3）；
    - 权限 can_write：非 owner 且非 super-admin → 403（AC-8.1）；
    - 并发 last-write-wins（后写覆盖）+ 每次都留审计记录（AC-20.2）；
    - 改名会同步把磁盘文件重命名（磁盘名内嵌 name，须保持可重建）。
    """
    if content is None and name is None:
        raise HTTPException(status_code=400, detail="provide content or name")

    with _lock:
        _ensure_schema()
        conn = db.get_conn()
        row = _select_doc(conn, doc_id)
        if row is None:
            raise HTTPException(status_code=404, detail="document not found: " + str(doc_id))
        existing = _row_to_doc(row)
        if not is_text_mime(existing["mime"]):
            raise HTTPException(
                status_code=400,
                detail="binary format ({0}) cannot be edited online; use overwrite upload instead".format(
                    existing["mime"]
                ),
            )
        if not can_write(actor, existing):
            raise HTTPException(
                status_code=403,
                detail="not allowed to edit doc owned by {0}({1})".format(
                    existing["owner"], existing["owner_type"]
                ),
            )

        old_path = resolve_disk_path(existing)
        new_name = existing["name"]
        actions = []
        parts = []

        if name is not None:
            candidate = safe_component(name)
            if not os.path.splitext(candidate)[1]:
                candidate = candidate + DEFAULT_TEXT_EXT
            new_mime = guess_mime(candidate)
            if not is_text_mime(new_mime):
                raise HTTPException(
                    status_code=400, detail="rename must keep a text format (txt/md/json/csv)"
                )
            if candidate != existing["name"]:
                new_name = candidate
                actions.append("rename")
                parts.append("改名 {0} → {1}".format(existing["name"], new_name))

        new_doc = dict(existing)
        new_doc["name"] = new_name
        new_path = resolve_disk_path(new_doc)
        new_mime = guess_mime(new_name)

        if new_path != old_path and os.path.isfile(old_path):
            os.replace(old_path, new_path)       # 改名：磁盘文件同步重命名，保持可重建

        if content is not None:
            data = content.encode("utf-8")
            if len(data) > MAX_UPLOAD_SIZE:
                raise HTTPException(status_code=413, detail="content too large")
            with open(new_path, "wb") as f:      # 重写正文（last-write-wins）
                f.write(data)
            new_size = len(data)
            actions.append("edit")
            parts.append("正文 {0} → {1} 字节".format(existing["size"], new_size))
        else:
            new_size = os.path.getsize(new_path) if os.path.isfile(new_path) else existing["size"]

        action = "rename" if actions == ["rename"] else "edit"
        summary = "{0} {1}".format(new_name, "；".join(parts) if parts else "无实质变化")
        ts = now_iso()

        def _mut(c):
            c.execute(
                "UPDATE documents SET name=?, mime=?, size=?, updated_at=? WHERE id=?",
                (new_name, new_mime, new_size, ts, doc_id),
            )
            _record_change(c, doc_id, actor, action, summary)
            return None

        _txn(_mut)
        return public_doc(_row_to_doc(_select_doc(conn, doc_id)))


def delete_doc(doc_id: str, actor: dict) -> dict:
    """删除文档：删磁盘文件 + 删 documents 行 + 记 change(delete)。

    权限 can_write（owner_type+owner 双校验 / 人类 super-admin / SUPER_ADMINS）；
    越权 → 403（Agent 只能删自己 owner 的文件，老板 02:16 定，AC-10.3/10.4）。
    document_changes 保留该 doc 的历史记录（审计留痕，无外键约束）。
    """
    with _lock:
        _ensure_schema()
        conn = db.get_conn()
        row = _select_doc(conn, doc_id)
        if row is None:
            raise HTTPException(status_code=404, detail="document not found: " + str(doc_id))
        existing = _row_to_doc(row)
        if not can_write(actor, existing):
            raise HTTPException(
                status_code=403,
                detail="not allowed to delete doc owned by {0}({1})".format(
                    existing["owner"], existing["owner_type"]
                ),
            )
        path = resolve_disk_path(existing)

        def _mut(c):
            c.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            _record_change(c, doc_id, actor, "delete", "删除 {0}".format(existing["name"]))
            return None

        _txn(_mut)
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass
        _notify("delete", actor, doc_id, existing["name"], with_link=False)
        return {"status": "ok", "id": doc_id, "name": existing["name"]}
