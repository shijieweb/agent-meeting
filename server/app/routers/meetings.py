# -*- coding: utf-8 -*-
"""R4 纪要 + R5 任务卡 · 会议产出闭环（最小实现）。

端点：
- POST /api/meetings/summarize   触发纪要生成（从历史消息提取决策/待办）
- GET  /api/meetings             列出所有纪要文件
- GET  /api/meetings/<filename>  查看单条纪要

落盘路径：dev-work/meetings/<YYYY-MM-DD>-<session_id>.md
"""
import os
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger("am_meetings")
router = APIRouter(prefix="/api/meetings", tags=["meetings"])

# 纪要落盘路径：workbuddy/dev-work/meetings/（从 __file__ 往上 5 层到 workbuddy 根）
_WORKBUDDY_ROOT = Path(__file__).parent.parent.parent.parent.parent
MEETINGS_DIR = _WORKBUDDY_ROOT / "dev-work" / "meetings"
MEETINGS_DIR.mkdir(parents=True, exist_ok=True)

# ---- 请求/响应模型 ----

class SummarizeRequest(BaseModel):
    """触发纪要生成。"""
    session_id: str                    # 会议会话 ID（用于落盘文件名）
    last_n: int = 50                   # 从最近 N 条消息中提取
    project_id: Optional[int] = None   # 可选：接看板项目 ID（R5）


class SummarizeResponse(BaseModel):
    ok: bool
    filename: str
    decisions: List[str]
    todos: List[str]
    task_ids: List[int] = []         # R5：若接入看板，返回新建任务 ID


class MeetingSummary(BaseModel):
    filename: str
    created_at: str
    decisions: List[str]
    todos: List[str]


# ---- 纯函数：从消息文本提取决策/待办 ----

def extract_decisions_and_todos(messages: List[str]) -> tuple[List[str], List[str]]:
    """从消息列表提取决策和待办。

    启发式规则（最小实现，不依赖 LLM）：
    - 决策：以"决定"/"确定"/"结论"/"方案"开头的句子
    - 待办：以"让XX"/"XX负责"/"去做"/"需要"/"TODO"/"待办"开头的句子，或带人名+动作的结构
    """
    decisions = []
    todos = []

    for msg in messages:
        text = msg.get("content", "") if isinstance(msg, dict) else str(msg)
        text = text.strip()
        if not text:
            continue

        # 决策模式
        if re.match(r'^(决定|确定|结论|方案| agreed|确认)', text, re.IGNORECASE):
            decisions.append(text)
            continue

        # 待办模式
        if re.match(r'^(让|.*负责|去做|需要|TODO|待办|task|assign)', text, re.IGNORECASE):
            todos.append(text)
            continue

        # 兜底：含"让" + 人名 + 动作
        if '让' in text and re.search(r'让\s+\w+\s+[去负责|做|完成|处理]', text):
            todos.append(text)

    return decisions, todos


def format_summary(session_id: str, decisions: List[str], todos: List[str]) -> str:
    """格式化为 Markdown 纪要。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# 会议纪要 · {session_id}",
        f"",
        f"**时间**：{now}",
        f"",
        f"## 决策",
        f"",
    ]
    if decisions:
        for d in decisions:
            lines.append(f"- {d}")
    else:
        lines.append("_无明确决策_")

    lines.extend([
        f"",
        f"## 待办",
        f"",
    ])
    if todos:
        for t in todos:
            lines.append(f"- [ ] {t}")
    else:
        lines.append("_无待办事项_")

    lines.append("")
    return "\n".join(lines)


def save_summary(session_id: str, content: str) -> str:
    """保存纪要到 dev-work/meetings/。<回文件名">"""
    ts = datetime.now().strftime("%Y-%m-%d")
    filename = f"{ts}-{session_id}.md"
    filepath = MEETINGS_DIR / filename
    filepath.write_text(content, encoding="utf-8")
    return filename


def load_summary(filename: str) -> str:
    """加载单条纪要。"""
    filepath = MEETINGS_DIR / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"纪要不存在: {filename}")
    return filepath.read_text(encoding="utf-8")


def list_summaries() -> List[MeetingSummary]:
    """列出所有纪要。"""
    result = []
    if not MEETINGS_DIR.exists():
        return result
    for f in sorted(MEETINGS_DIR.glob("*.md"), reverse=True):
        content = f.read_text(encoding="utf-8")
        # 解析 frontmatter 提取 decisions/todos
        decisions = [line.lstrip("- ") for line in content.split("\n") if line.startswith("- ")]
        todos = [line.lstrip("- ") for line in content.split("\n") if line.startswith("- [ ] ")]
        result.append(MeetingSummary(
            filename=f.name,
            created_at=f.stat().st_mtime,
            decisions=decisions,
            todos=todos,
        ))
    return result


# ---- 端点 ----

@router.post("/summarize", response_model=SummarizeResponse)
def summarize_meeting(req: SummarizeRequest):
    """从最近消息提取决策/待办，生成纪要落盘。

    当前为启发式提取（不依赖 LLM），后续可接入 agnes-2.0-flash 做智能摘要。
    """
    from app.services import message_store
    # 拉取最近 N 条消息（按时间倒序，取最后 N 条）
    history = message_store.get_history(limit=max(req.last_n, 50))
    # history 是升序，取最后 last_n 条
    recent_messages = history[-req.last_n:] if len(history) > req.last_n else history
    messages_list = [{"content": m.get("content", ""), "sender": m.get("sender_name", "")}
                     for m in recent_messages]

    decisions, todos = extract_decisions_and_todos(messages_list)
    content = format_summary(req.session_id, decisions, todos)
    filename = save_summary(req.session_id, content)

    task_ids = []
    if req.project_id:
        # R5: 待办 → 看板任务（简化：仅记录，不实际调用看板 API）
        logger.info(f"R5 待办转任务：project_id={req.project_id}, todos={todos}")
        # TODO: 调用 shared_board API 创建任务

    return SummarizeResponse(
        ok=True,
        filename=filename,
        decisions=decisions,
        todos=todos,
        task_ids=task_ids,
    )


@router.get("", response_model=List[MeetingSummary])
def list_meetings():
    """列出所有会议纪要。"""
    return list_summaries()


@router.get("/{filename}")
def get_meeting(filename: str):
    """查看单条纪要。"""
    return load_summary(filename)
