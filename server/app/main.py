# -*- coding: utf-8 -*-
"""FastAPI 入口：挂载路由与静态文件。对应方案书 §4.2 app/main.py。"""
import os

# ── R1 令牌闸（T-am-hardening-r1）：最小 .env 读取（密钥绝不硬编码）──
# 8000 启动环境（run.bat / run.sh / uvicorn）不自动加载 .env，这里在进程启动期
# 从候选路径读取 AM_* 环境变量注入 os.environ（仅当该变量尚未被命令行/系统设置，
# 即命令行 env 优先）。找不到文件或缺失字段均静默跳过，绝不抛错、绝不硬编码密钥。
def _load_am_env():
    """从 agent-meeting/.env 或 ~/.workbuddy/.env 读取 AM_* 变量注入 os.environ。"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(base_dir, "..", ".env"),        # agent-meeting/.env
        os.path.expanduser("~/.workbuddy/.env"),     # ~/.workbuddy/.env
    ]
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    key, val = key.strip(), val.strip().strip('"').strip("'")
                    # 仅注入 AM_ 前缀的令牌相关变量，且不覆盖已存在的环境变量
                    if key.startswith("AM_") and key not in os.environ:
                        os.environ[key] = val
        except FileNotFoundError:
            continue
        except OSError:
            continue


_load_am_env()

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

from app.config import OFFLINE_WINDOW_SECONDS, LOST_TIMEOUT
from app.routers import agents, docs, messages

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "app", "static")

# 防移动端缓存旧静态资源导致版本偏斜（回归B）。
# StaticFiles 默认只发 etag/last-modified，不强制重新校验；
# 这里给所有 /static 响应加 no-cache/must-revalidate，配合 index.html 内 ?v= 版本号使用。
STATIC_CACHE_HEADERS = {
    "Cache-Control": "no-cache, must-revalidate",
    "Pragma": "no-cache",
}


class CacheControlledStaticFiles(StaticFiles):
    """带强制重新校验头的静态文件服务。"""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response is not None and hasattr(response, "headers"):
            for key, value in STATIC_CACHE_HEADERS.items():
                response.headers[key] = value
        return response


app = FastAPI(title="Agent Hub", version="1.0 (MVP)")

app.include_router(agents.router)
app.include_router(messages.router)
app.include_router(docs.router)

app.mount(
    "/static",
    CacheControlledStaticFiles(directory=STATIC_DIR),
    name="static",
)


@app.get("/")
def index():
    return FileResponse(
        os.path.join(STATIC_DIR, "index.html"),
        headers=STATIC_CACHE_HEADERS,
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def get_config():
    """F-g.2 / Q5：透出前端离线着色窗口等配置（避免前端硬编码阈值，design §0.7/§5.4）。"""
    return {
        "offline_window": OFFLINE_WINDOW_SECONDS,   # session=1 时离线判定窗口（7200s）
        "online_window": LOST_TIMEOUT,              # 非 session 在线窗口（1200s）
        "lost_timeout": LOST_TIMEOUT,               # 失联阈值（1200s）
    }
