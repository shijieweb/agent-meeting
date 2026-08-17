# -*- coding: utf-8 -*-
"""FastAPI 入口：挂载路由与静态文件。对应方案书 §4.2 app/main.py。"""
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

from app.routers import agents, messages

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
