# -*- coding: utf-8 -*-
"""pytest 配置（隔离铁律：红线中的红线）。

所有测试强制指向独立 DATA_DIR（生产 8000 / server/data/ 零触碰），端口 8012 语义由
TestClient 等价替代（进程内、DATA_DIR 隔离，不启动任何 8000 实例）。
env 必须在任何 app 导入前设置，保证 config.DATA_DIR / db.DATA_DIR 指向隔离目录。
"""
import os
import sys
import shutil
import glob
import tempfile

# ---- 隔离 DATA_DIR（先于一切 app 导入）----
ISO_DIR = tempfile.mkdtemp(prefix="am_v6_iso_")
os.environ["DATA_DIR"] = ISO_DIR
os.environ["SWEEP_INTERVAL"] = "0"

# server/ 加入 sys.path，便于 import migrate / app
HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.normpath(os.path.join(HERE, ".."))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

import pytest  # noqa: E402

SERVER_DATA = os.path.normpath(os.path.join(SERVER_DIR, "data"))


@pytest.fixture(scope="session", autouse=True)
def _seed_session():
    """拷贝生产 JSON 样本到隔离 DATA_DIR（只读复制，不烧生产）。"""
    for base in ["agents.json", "messages.json", "reads.json"]:
        src = os.path.join(SERVER_DATA, base)
        if os.path.isfile(src):
            shutil.copy(src, os.path.join(ISO_DIR, base))
    for fp in glob.glob(os.path.join(SERVER_DATA, "agent_read_*.json")):
        shutil.copy(fp, ISO_DIR)
    yield
    # 清理隔离目录
    try:
        shutil.rmtree(ISO_DIR, ignore_errors=True)
    except Exception:
        pass


@pytest.fixture
def fresh_db():
    """每测试清空库并重新迁移（从 ISO_DIR 样本），保证无状态泄漏。"""
    import app.services.db as dbmod
    # 关闭单例连接并释放文件锁（Windows 下否则 os.remove 报 PermissionError）
    if dbmod._CONN is not None:
        try:
            dbmod._CONN.close()
        except Exception:
            pass
        dbmod._CONN = None
    db_path = os.path.join(ISO_DIR, "agent_meeting.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    import migrate
    rc = migrate.main([])
    assert rc == 0, "migrate failed rc=%s" % rc
    return ISO_DIR


@pytest.fixture
def client(fresh_db):
    """隔离实例 TestClient（等价端口 8012，DATA_DIR 隔离，生产零触碰）。"""
    from app.main import app
    from fastapi.testclient import TestClient
    return TestClient(app)
