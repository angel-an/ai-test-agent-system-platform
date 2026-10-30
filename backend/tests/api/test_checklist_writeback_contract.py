# -*- coding: utf-8 -*-
"""写回列契约单测（LinkFox 复核建议）：锁死「两侧同步的 12 列定义」。

platform 的 WRITEBACK_COLUMNS 与腾讯文档「自动化执行记录」表头一一对应，
是最容易在改一处忘一处时静默漂移的契约——此测试让任何一侧改列数/列名/取值
逻辑时立刻红。
"""
from app.services.checklist_sync import (
    WRITEBACK_COLUMNS,
    build_writeback_payload,
    VERDICT_LABEL,
)


EXPECTED_COLUMNS = [
    "执行时间", "执行方式", "项目画像", "主模块", "判定", "通过", "失败", "未验证",
    "流程类跳过", "失败行", "报告链接", "run_id",
]


def test_writeback_columns_match_tencent_doc_header():
    assert list(WRITEBACK_COLUMNS) == EXPECTED_COLUMNS


def test_payload_has_exactly_the_contract_columns():
    run = {
        "run_id": "r1", "run_verdict": "pass", "profile_name": "p",
        "summary": {"pass": 1, "fail": 0, "unverified": 0}, "skipped_flow": 0,
        "report_url": "", "created_at": "2026-10-29T23:36:28+08:00",
    }
    payload = build_writeback_payload(run, [{"row_id": "CK-x", "status": "pass",
                                             "main_module": "活动相关"}])
    assert list(payload.keys()) == EXPECTED_COLUMNS  # 键序即列序，逐列对齐


def test_main_module_dedup_from_results():
    run = {"run_verdict": "pass", "summary": {}, "created_at": ""}
    results = [
        {"row_id": "a", "status": "pass", "main_module": "首页"},
        {"row_id": "b", "status": "pass", "main_module": "首页"},
        {"row_id": "c", "status": "fail", "main_module": "我的"},
        {"row_id": "d", "status": "pass"},  # 无 main_module → 跳过
    ]
    payload = build_writeback_payload(run, results)
    assert payload["主模块"] == "首页、我的"


def test_main_module_explicit_override_wins():
    run = {"run_verdict": "pass", "summary": {}, "created_at": "",
           "main_module": "活动相关-营销事件"}
    results = [{"row_id": "a", "status": "pass", "main_module": "活动相关"}]
    payload = build_writeback_payload(run, results)
    assert payload["主模块"] == "活动相关-营销事件"


def test_verdict_label_and_failed_rows():
    run = {"run_verdict": "fail", "summary": {"fail": 1}, "created_at": ""}
    results = [{"row_id": "CK-9", "status": "fail", "feature": "图文广告",
                "main_module": "首页", "error": "落地不符"}]
    payload = build_writeback_payload(run, results)
    assert payload["判定"] == VERDICT_LABEL["fail"] == "失败"
    assert "CK-9" in payload["失败行"] and "落地不符" in payload["失败行"]


def test_time_converted_to_cn_timezone():
    run = {"run_verdict": "pass", "summary": {}, "created_at": "2026-10-29T15:36:28+00:00"}
    payload = build_writeback_payload(run, [])
    assert payload["执行时间"] == "2026-10-29 23:36:28"
