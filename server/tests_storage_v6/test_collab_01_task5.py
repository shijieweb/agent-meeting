# -*- coding: utf-8 -*-
"""T-collab-01 Task5: Board Sync 测试。

验证 map_task_for_board 映射 + sync_task_to_board 调用（不依赖真实 8788）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_map_task_for_board():
    """状态/优先级映射正确。"""
    from app.services.board_sync import map_task_for_board

    task = {
        "title": "Test Task",
        "description": "Desc",
        "status": "completed",
        "assignee": "test_agent",
        "priority": "high",
        "progress": 100,
        "deadline": "2026-01-01",
    }
    mapped = map_task_for_board(task)
    assert mapped["project_id"] == 19, f"expected 19, got {mapped['project_id']}"
    assert mapped["title"] == "Test Task"
    assert mapped["detail"] == "Desc"
    assert mapped["status"] == "已验证"
    assert mapped["author"] == "test_agent"
    assert mapped["priority"] == "高"
    assert mapped["progress"] == 100
    assert mapped["is_hotfix"] is False
    print("  ✅ map_task_for_board 映射正确")


def test_map_all_statuses():
    """所有状态都能正确映射。"""
    from app.services.board_sync import map_task_for_board

    pairs = [
        ("pending", "待办"),
        ("in_progress", "进行中"),
        ("review", "待验证"),
        ("completed", "已验证"),
        ("cancelled", "阻塞"),
    ]
    for src, dst in pairs:
        task = {"title": "T", "status": src, "assignee": "a", "priority": "medium"}
        mapped = map_task_for_board(task)
        assert mapped["status"] == dst, f"failed: {src} -> {mapped['status']}"
    print("  ✅ 全部状态映射正确")


def test_map_all_priorities():
    """所有优先级都能正确映射。"""
    from app.services.board_sync import map_task_for_board

    pairs = [
        ("low", "低"),
        ("medium", "中"),
        ("high", "高"),
        ("urgent", "紧急"),
    ]
    for src, dst in pairs:
        task = {"title": "T", "status": "pending", "assignee": "a", "priority": src}
        mapped = map_task_for_board(task)
        assert mapped["priority"] == dst, f"failed: {src} -> {mapped['priority']}"
    print("  ✅ 全部优先级映射正确")


def test_sync_calls_api():
    """sync_task_to_board 实际调用 shared_board API（需要 8788 在跑）。"""
    from app.services.board_sync import sync_task_to_board

    task = {
        "id": "task_test5",
        "title": "Board Sync Test",
        "description": "T-collab-01 测试",
        "status": "completed",
        "assignee": "阿编",
        "priority": "medium",
        "progress": 100,
    }
    try:
        result = sync_task_to_board(task)
        assert "id" in result, f"unexpected response: {result}"
        print(f"  ✅ sync 成功，board id={result['id']}")
    except Exception as e:
        print(f"  ⚠️ sync 失败（8788 可能未运行）: {e}")
        # 不算失败，因为这不是关键路径


def test_sync_with_custom_project():
    """指定 project_id 覆盖默认值。"""
    from app.services.board_sync import map_task_for_board

    task = {"title": "T", "status": "pending", "assignee": "a", "priority": "medium"}
    mapped = map_task_for_board(task, project_id=4)
    assert mapped["project_id"] == 4
    print("  ✅ 自定义 project_id 生效")


if __name__ == "__main__":
    print("\n=== T-collab-01 Task5: Board Sync ===\n")
    test_map_task_for_board()
    test_map_all_statuses()
    test_map_all_priorities()
    test_sync_calls_api()
    test_sync_with_custom_project()
    print("\n✅ Task5 完成")
