# -*- coding: utf-8 -*-
"""删除用例时的级联契约（2026-10-22 实测缺陷回归）。

缺陷现象：`DELETE /api/v2/projects/PR-2/test-cases/{id}` 对**跑过**的用例恒 500：

    null value in column "test_case_id" of relation "test_results"
    violates not-null constraint

根因：`TestResult.test_case` 用的是 `backref="test_results"`，**没有 cascade**。
SQLAlchemy 对「非空外键 + 无 cascade」的默认动作是**把外键置 NULL**，
而不是让数据库的 `ondelete="CASCADE"` 生效。于是 ORM 先 UPDATE 置 NULL → 撞非空约束。

隐蔽点：只有**已有执行结果**的用例才删不掉，没跑过的能正常删——所以长期没被发现。
本次 55 条问卷用例里有 19 条带 `test_results`，一半删不掉才暴露出来。
"""
import pytest
from sqlalchemy import inspect

from app.models.test_case import TestCase
from app.models.test_result import TestResult


@pytest.mark.parametrize("attr", ["test_results", "test_run_cases", "steps",
                                  "api_tests", "web_tests"])
def test_deleting_case_cascades_to_all_owned_children(attr) -> None:
    """删用例必须级联删除所有子实体，不能把外键置 NULL。"""
    rel = inspect(TestCase).relationships[attr]
    assert "delete" in rel.cascade, (
        f"{attr} 没有 delete cascade —— 删除用例会尝试把外键置 NULL，"
        f"非空外键会直接 500（2026-10-22 实测）"
    )


def test_test_results_backref_has_delete_cascade() -> None:
    """`test_results` 是 backref 出来的，同样是删除链路上的必删子表。"""
    rel = inspect(TestCase).relationships["test_results"]
    assert "delete" in rel.cascade, (
        "test_results 缺 delete cascade：DELETE /test-cases/{id} 对跑过的用例会 500\n"
        "修法：TestResult.test_case 的 backref 改成 "
        'backref("test_results", cascade="all, delete-orphan")'
    )


def test_result_fk_is_non_nullable_so_nullifying_is_illegal() -> None:
    """反证：外键是非空的，所以「置 NULL」这条默认路径注定失败，必须走 DELETE。"""
    col = TestResult.__table__.c.test_case_id
    assert col.nullable is False
