"""rev148：第二批通道能力（静态模板断言 + 解析层单测）。

① ``list_checks[].action``：行内操作 + Popconfirm/Modal 双形态确认 + 操作后回读；
② ``list_checks[].readonly``：查看只读（全禁用才 pass）；
③ ``export_checks``：独立导出通道（弹窗/空时间段校验/导出中心/下载/列名出现性）。
判定口径（诚实边界）：解压失败/轮询超时/下载缺失一律 unverified。
"""
import ast
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.web_case_compiler import (  # noqa: E402
    COMPILER_REV,
    parse_export_checks,
    parse_list_checks,
)


def _py() -> str:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "app", "services", "web_case_compiler.py")
    with open(path, encoding="utf-8") as f:
        return f.read()


def _ts() -> str:
    import app.services.web_case_compiler as m
    return m._FORM_EXECUTOR_TS


# ---------- 版本 ----------

def test_rev_bumped():
    src = _py()
    assert 'COMPILER_REV = "rev161"' in src
    ast.parse(src)


# ---------- 解析层 ----------

def test_export_checks_parse_fields_and_defaults():
    items, rejects = parse_export_checks([
        {"list_url": "https://x/list", "expect_columns": ["問卷標題", "問卷編碼"], "case_id": "TC-1"},
    ])
    assert not rejects and len(items) == 1
    it = items[0]
    assert it["menu"] == "更多" and it["label"] == "导出明细"
    assert it["validate_empty_time"] is True and it["pick_dates"] is True
    assert it["export_center"]["status_text"] == "已完成|完成"
    assert it["export_center"]["timeout_ms"] == 180000
    assert it["filename_regex"]
    # rev157：任务名与文件名是两个字符串，导出中心用独立正则；且只认本轮新任务
    assert it["center_name_regex"] == "调查问卷-|問卷調查-"
    assert it["fresh_tolerance_s"] == 180
    assert "xlsx" not in it["center_name_regex"]  # 任务名没有 .xlsx，带了就恒不命中
    assert it["expect_columns"] == ["問卷標題", "問卷編碼"]
    assert it["case_id"] == "TC-1" and it["rule_ref"] == "导出明细"


def test_export_checks_rejects_are_visible():
    items, rejects = parse_export_checks(["bad", {"label": "导出明细"}, {"label": "导出明细"}])
    assert len(items) == 1
    assert len(rejects) >= 2  # 非对象 + 重复 label
    assert all(r.get("reason") for r in rejects)


def test_list_checks_action_and_readonly_passthrough():
    items, rejects = parse_list_checks([
        {"value": "V1", "action": {"label": "禁用", "confirm": "確定"}},
        {"value": "V2", "readonly": True},
        {"value": "V3", "action": {"label": " "}},   # 空 label → action 丢弃，检查保留
    ])
    assert not rejects and len(items) == 3
    # rev156：action 增加 labels 候选标签（缺省空数组，老配置行为不变）
    assert items[0]["action"] == {"label": "禁用", "labels": [], "menu": "",
                                 "confirm": "確定", "post_value": ""}
    assert items[1]["readonly"] == {"view_label": "查看|檢視", "view_menu": ""}
    assert items[2]["action"] is None


# ---------- 执行器模板（静态断言）----------

def test_form_phase_skips_both_standalone_types():
    ts = _ts()
    assert "check.type === 'list_visible' || check.type === 'export_check'" in ts


def test_action_uses_dual_confirm_shapes():
    """antd 确认框两种形态必须同时采样（2026-10-20 问卷误报 931769 的教训）。"""
    ts = _ts()
    assert ".ant-popover:not(.ant-popover-hidden), .ant-modal-confirm" in ts
    assert "LIST_ACTION_TRACE" in ts


def test_action_rescans_after_operation():
    """操作后必须重按搜索回读，以回读结果判 expect，不能用操作前快照。"""
    ts = _ts()
    assert "actionNote" in ts
    assert "post_found" in ts


def test_readonly_counts_and_pass_bar():
    ts = _ts()
    assert "READONLY_TRACE" in ts
    assert "inputs_disabled === roState.inputs_total" in ts
    assert "selects_disabled === roState.selects_total" in ts


def test_export_channel_end_to_end_pieces():
    ts = _ts()
    for marker in (
        "runExportChecks",
        "EXPORT_TRACE",
        "EXPORT_EVIDENCE",
        "EXPORT_PHASE",
        "[export-skip]",
        "空时间段未拦截",
        "导出中心",  # 轮询超时提示
        "未捕获下载",
        "列名缺失",
    ):
        assert marker in ts, marker


def test_export_columns_is_presence_check_not_order():
    """诚实边界：列名是出现性校验（indexOf），绝不冒充列序校验。"""
    ts = _ts()
    assert "text.indexOf(c) >= 0 ? columns.found : columns.missing" in ts
    assert "文件解析失败，列名校验未执行" in ts


def test_export_zip_text_reads_central_directory():
    """data descriptor 成员（local header 尺寸为 0）必须走中央目录，否则抽不出任何文本。

    rev161 实测：旧顺序扫 local header 在首个成员 csize=0 处 break，text 长度 0；
    改中央目录后 9/9 列名命中。
    """
    ts = _ts()
    assert "0x06054b50" in ts                      # EOCD
    assert "0x02014b50" in ts                      # central directory header
    assert "buf.readUInt32LE(eocd + 16)" in ts     # 中央目录起始偏移
    assert "if (csize && dataStart + csize <= buf.length) {" in ts   # 越界守卫（非 break）
    assert "if (!csize || dataStart + csize > buf.length) break;" not in ts
    assert "inflateRawSync" in ts


def test_export_download_degrades_honestly():
    ts = _ts()
    assert "运行环境无 fs 模块，未落盘" in ts


def test_summary_reports_export_phase():
    ts = _ts()
    assert "export_phase: {" in ts
    assert "rejected_at_plan: (FORM_PLAN.export_check_rejects || []).length" in ts


def test_payload_whitelist_carries_export_rejects():
    src = _py()
    assert '"export_check_rejects": plan.get("export_check_rejects") or []' in src


def test_plan_returns_carry_export_rejects():
    src = _py()
    assert src.count('"export_check_rejects": export_check_rejects') >= 2  # 早退分支 + 主分支


# ---------- rev157：导出通道三个「假自证」 ----------

def test_export_center_uses_task_name_regex_not_filename():
    """任务名（无 .xlsx）与下载文件名（有 .xlsx）必须用两个正则，否则轮询恒不命中。"""
    ts = _ts()
    assert "check.center_name_regex" in ts
    # 旧写法：轮询直接拿 filename_regex 当任务名正则 → 恒不命中
    assert "const rxName = new RegExp(String(check.filename_regex" not in ts
    assert "dl.name_matched = new RegExp(String(check.filename_regex || '.')).test(dl.suggested)" in ts


def test_export_center_requires_fresh_task():
    """历史行同样满足「已完成 + 名称前缀」，不卡时间会把没发出的导出判成通过。"""
    ts = _ts()
    assert "const confirmAt = Date.now();" in ts
    assert "rowTimeMs(r) >= confirmAt - tolerance" in ts
    assert "center.fresh_after = confirmAt;" in ts


def test_export_pick_dates_clicks_twice_then_reads_back():
    """RangePicker 只点一次输入框仍为空 ⇒ 点确定被拦截、请求不发；必须回读。"""
    ts = _ts()
    assert "clickDayInPanel" in ts
    assert "ant-calendar-range-part" in ts               # 面板是两个月并排，取左面板的当月
    assert "last-month|next-month|disabled-cell" in ts   # class 在 <td> 上，不在 .ant-calendar-date 上
    assert "datesPicked = range.length >= 2;" in ts
    assert "时间段未选上（输入框无值，导出请求不会被发出）" in ts


def test_export_researches_before_locating_row():
    """列表页保留上一阶段搜索条件（goto 同 hash 是 no-op）⇒ 导出必须先按 value 重新检索。"""
    ts = _ts()
    assert "rootUsed" in ts
    assert "stage: 'searched'" in ts
    assert "rootUsed = await pickRoot();" in ts


def test_export_columns_decode_html_entities():
    """本 SUT 导出的 xlsx 把中文写成 HTML 数字实体（&#22635; = 填），不解码则列名恒 missing。"""
    ts = _ts()
    assert "const decodeEntities" in ts
    assert "String.fromCodePoint(parseInt(d, 10))" in ts
    assert "decodeEntities(zipText(buf) + buf.toString('latin1')" in ts


def test_action_failure_is_not_pass():
    """配置了行内动作却没点到 → 不得判 pass（TR-772 的 action_not_found 曾被 pass 掩盖）。"""
    ts = _ts()
    assert "let actionFailed = '';" in ts
    assert "if (found && actionFailed) {" in ts
    assert "列表可见但行内操作未完成" in ts
    assert "else if (act.confirm && !conf.ok) actionFailed" in ts
