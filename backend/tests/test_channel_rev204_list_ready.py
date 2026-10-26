# -*- coding: utf-8 -*-
"""rev204：列表「就绪」与「有数据」解耦 —— 采样不得早于数据落地。

背景（2026-10-26，用户问「店铺列表为什么 0 行数据，测试时一直有数据」）：
TR-830 的日志是

    YL_LIST_READY {"rendered":true,"attempts":3,"elapsedMs":1273}
    COLUMN_HEADERS {"count":16, ...}
    SORT_TRACE {"verdict":"unverified","why":"no_rows"}

而同一页面、同账号实测：`/org/shop/list` 返回 10 条、DOM 也有 10 行 → **数据一直在**。
根因是就绪判据只看 `.ant-table` 可见：antd 先渲染 thead、数据接口回来后才挂 tbody，
判据在 thead 出现那一瞬（1.27s）就通过，紧随其后的排序采样正好落在空窗里。

本文件守两件事：
1. `YL_LIST_READY` 判据 = 表格可见 **且**（有数据行 或 有空态提示），日志带 rows/empty；
2. `sortColumnSample` 无数据行时轮询等待（默认 12s），超时才判 no_rows 且带 waitedMs。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.web_case_link_service import WebCaseLinkService

SVC = WebCaseLinkService.__new__(WebCaseLinkService)


def _render() -> str:
    case = SimpleNamespace(
        name="列表顯示-店鋪-欄位完整性校驗",
        module="YL-主數據管理-店鋪",
        description=None,
        identifier="TC-307483",
    )
    return SVC._generate_yl_masterdata_script(
        case, "https://console.uat.eatizen.com.hk", ["Master Data Management"],
        columns_json="null", sort_checks_json="[]",
    )


def test_list_ready_requires_rows_or_empty_state():
    """就绪判据必须包含数据行/空态，不能只判表格骨架。"""
    src = _render()
    assert "const listProbe = async () =>" in src
    assert ".ant-table-tbody > tr, tbody > tr" in src
    assert ".ant-table-placeholder, .ant-empty" in src
    # 关键：ready 由「有行 或 空态」决定，而不是 tableVisible 单独决定
    assert "return { ready: rows > 0 || emptyVisible, rows, empty: emptyVisible }" in src.replace(
        "{{", "{").replace("}}", "}")


def test_list_ready_log_carries_row_count():
    """日志要带 rows / empty，否则下次又只能靠猜「到底有没有数据」。"""
    src = _render()
    lines = [ln for ln in src.splitlines() if "YL_LIST_READY ${" in ln or "YL_LIST_READY \u0024{" in ln]
    assert lines, "未找到 YL_LIST_READY 日志行"
    assert any("rows: listState.rows" in ln and "empty: listState.empty" in ln for ln in lines)


def test_sort_sample_waits_for_rows_before_no_rows():
    """tbody 无行时要轮询等待，不能一次快照就判 no_rows。"""
    src = _render()
    assert "waitRowsMs" in src
    assert "while (!dataRows.length && Date.now() - t0 < waitMs)" in src
    assert "why: 'no_rows', waitedMs: Date.now() - t0" in src


def test_sort_trace_exposes_waited_ms():
    """SORT_TRACE 输出 waitedMs，便于分辨「真没数据」和「等过但没等到」。"""
    src = _render()
    # 该日志跨三行拼接（SORT_TRACE 行 + why 行 + waitedMs 行），按片段断言
    assert "why: before.why" in src
    assert "waitedMs: before.waitedMs" in src
