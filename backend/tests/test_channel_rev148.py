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
    parse_filter_checks,
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
    assert 'COMPILER_REV = "rev170"' in src
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


# ---------- rev162：row_pick（数据自消耗） ----------

def test_row_pick_parse_and_label():
    """动作会推进状态（待启用→启用→禁用终态），写死 value 跑几次就失效 ⇒ 支持按列筛行。"""
    from app.services.web_case_compiler import parse_row_pick
    rp = parse_row_pick({"column": "状态", "in": "待启用", "value_column": "问卷标题"})
    assert rp == {"column": "状态", "in": ["待启用"],
                  "value_column": "问卷标题", "pick": "first"}
    # 缺省列名/缺省取首列
    assert parse_row_pick({"in": ["待启用"]})["column"] == "状态"
    assert parse_row_pick({"in": ["待启用"]})["value_column"] == ""
    # 没有 in ⇒ 不是有效 row_pick（不能退化成「随便取一行」）
    assert parse_row_pick({}) is None
    assert parse_row_pick("状态") is None


def test_list_checks_accepts_row_pick_without_value():
    """value 与 row_pick 二选一；都没有才拒绝。"""
    items, rejects = parse_list_checks([
        {"row_pick": {"column": "状态", "in": ["待启用"], "value_column": "问卷标题"},
         "action": {"label": "启用", "labels": ["启用", "禁用"], "confirm": "确 定"}},
    ])
    assert not rejects, rejects
    assert items[0]["value"] == ""
    assert items[0]["row_pick"]["in"] == ["待启用"]
    assert items[0]["pick_label"] == "状态∈[待启用]"
    assert items[0]["name"] == "list_visible:状态∈[待启用]"
    assert items[0]["action"]["labels"] == ["启用", "禁用"]

    _, rej2 = parse_list_checks([{"expect": "found"}])
    assert rej2 and "value 或 row_pick" in rej2[0]["reason"]


def test_row_pick_executor_prefers_exact_match():
    """in=["启用"] 不能命中「待启用」—— 状态机里两者相邻，包含匹配会选错行。"""
    ts = _ts()
    assert "LIST_PICK_TRACE" in ts
    assert "sp.in.indexOf(c) >= 0" in ts              # 精确优先
    assert "mode = 'exact'" in ts
    assert "mode = 'contains'" in ts                  # 精确全无才退化
    assert "no_row_matched" in ts                     # 未命中要留 cells 样本
    assert "val = String(pk.value);" in ts            # 命中后仍走原「搜索→扫描→动作」路径


def test_row_pick_ignores_cloned_fixed_column_body():
    """固定列会克隆出「每行只有 1 个 td」的 tbody，混在一起数下标会错位。"""
    ts = _ts()
    assert "bestCols" in ts
    assert "if (n > bestCols) { bestCols = n; dataRows = rs; }" in ts


def test_row_pick_reaches_form_plan():
    """parse 产出 row_pick 但 build_form_plan 不透传 ⇒ 执行器拿到空 value（TR-776 实证）。"""
    src = _py()
    i = src.index('"type": "list_visible"')
    blk = src[i:i + 1400]
    assert '"row_pick": item.get("row_pick")' in blk
    assert '"pick_label": item.get("pick_label")' in blk


def test_empty_value_never_reaches_scan():
    """空串会匹配任意行：found 恒真、动作点到第一行 —— 必须拦在搜索之前。"""
    ts = _ts()
    assert "if (!val) {" in ts
    assert "空串会匹配任意行" in ts


def test_row_pick_clears_previous_search_first():
    """列表保留上一检查项的搜索条件（goto 同 hash 是 no-op）⇒ pick 前必须清空重查。"""
    ts = _ts()
    assert "LIST_PICK_RESET" in ts
    assert "t.focus(); setter.call(t, '');" in ts
    assert "no_search_box" in ts


def test_required_error_samples_timing_separately():
    """rev165：清空后先不 blur 采样（onChange），拿不到新提示才 blur（onBlur）。

    探针实测（2026-10-21）：问卷标题/优先级清空后 120ms 即出现「请输入…」；
    题目标题/选项1 两种时机都无字段级提示，提交时才给 toast。
    """
    ts = _ts()
    assert "async function itemErrorTexts(" in ts
    assert "const errBase = await itemErrorTexts(modal, check.field, def);" in ts
    assert "const errOnChange = await itemErrorTexts(modal, check.field, def);" in ts
    assert "if (!freshOnChange.length) {" in ts          # onChange 命中就不必 blur
    assert "REQUIRED_TIMING" in ts
    assert "timing === 'on_change'" in ts or "timing = 'on_change'" in ts
    assert "timing = freshOnChange.length ? 'on_change'" in ts


def test_required_error_rejects_residual_hint_as_pass():
    """清空前已有的提示是残留，只有「新增文案」才算本次触发（旧行会把残留判 pass）。"""
    ts = _ts()
    assert "preexisting: errBase.length > 0" in ts
    assert "errOnChange.filter((t) => errBase.indexOf(t) < 0)" in ts
    assert "errBlur.filter((t) => errBase.indexOf(t) < 0)" in ts


def test_required_error_marks_has_error_without_text():
    """只挂 has-error、不渲文案的 SUT 不能被判成「无错误」（哨兵 + 兜底）。"""
    ts = _ts()
    assert "out.push('[has-error]')" in ts
    assert "timing = 'flag'" in ts


def test_superseded_process_record_not_counted_twice():
    """客户端 unverified 被提交层覆盖后不重复计入 total（TR-779：8/7/1 → 7/7/0）。"""
    src = _py()
    assert "deferred_superseded" in src
    assert "emitted_raw" in src
    assert "__DEFERRED_RE" in src
    # 对账行必须用 emitted_raw，否则 total 变小会被读成「少发了 1 条」
    assert "summary.emitted_raw - (__execA + __scenarioA + __submitA)" in src


def test_config_only_counts_by_source_not_case_id():
    """rev166：list_checks/export_checks 带自造 case_id（TC-VIEWONLY-REV157）但仍是
    配置驱动——config_only 判定只看 source，否则 plan 侧恒等式恒为 -3。"""
    ts = _ts()
    i = ts.index("config_only_checks:")
    blk = ts[i:i + 400]
    assert "c.source === 'locator_config'," in blk
    assert "case_id" not in blk.split(").length")[0]


# ---------- P2 配置 lint（2026-10-21，web_case_link_service 侧，不涉 COMPILER_REV） ----------

def _lint(plan):
    from app.services.web_case_link_service import config_lint_warnings
    return config_lint_warnings(plan)


def test_config_lint_detects_rev157_shape():
    """rev157 形态：center_name_regex 空（任务名匹配退化恒真）+ 空时间段无判据。"""
    plan = {"export_checks": [{"name": "export:导出明细", "download": True,
            "center_name_regex": "", "filename_regex": r"\.xlsx$",
            "validate_empty_time": True, "validation_toast": ""}]}
    rules = {w["rule"] for w in _lint(plan)}
    assert "export_center_name_regex_empty" in rules
    assert "export_filename_regex_empty" not in rules
    assert "export_empty_time_no_toast" in rules


def test_config_lint_detects_tr773_overlap_and_passes_real_config():
    """TR-773 形态：row_pick 状态候选与 action 文案零重叠；正确配置（含繁体）不误报。"""
    bad = {"list_checks": [{"row_pick": {"column": "状态", "in": ["待启用"],
            "value_column": "问卷标题"}, "action": {"label": "删除", "labels": ["刪除"]}}]}
    assert "list_row_pick_action_no_overlap" in {w["rule"] for w in _lint(bad)}
    ok = {"list_checks": [{"row_pick": {"column": "状态",
            "in": ["待启用", "待啟用"], "value_column": "问卷标题"},
            "action": {"label": "启用", "labels": ["启用", "禁用"]}}]}
    assert _lint(ok) == []


def test_config_lint_surfaces_run_tag_gap():
    """rev127 形态：expect_accept 场景里固定唯一编码没写 {{RUN_TAG}}。"""
    plan = {"run_tag_gap": [{"scenario_id": "s1", "field": "问卷标题",
                             "value": "XY1021161147G3Z", "hint": "x"}]}
    ws = _lint(plan)
    assert any(w["rule"] == "run_tag_gap" and "问卷标题" in w["target"] for w in ws)


def test_compile_receipt_includes_config_warnings():
    """回执必须携带结构化 config_warnings，并把 lint 行并入 warnings 文本。"""
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "app", "services", "web_case_link_service.py")
    with open(path, encoding="utf-8") as f:
        svc = f.read()
    assert "config_lint_warnings(form_plan)" in svc
    assert '"config_warnings": lint' in svc


def test_radio_helpers_follow_field_scope():
    """rev167：radioOption/optionLabels 走 scopeOf + hint——题目卡片在 .ant-form 外
    （TR-784：default_selected:题目类型 报未观测到，实为 modal 作用域找不到卡片）。"""
    ts = _ts()
    assert "const scope = scopeOf(modal, def);" in ts
    i = ts.index("function radioOption(")
    blk = ts[i:i + 700]
    assert "scopeOf(modal, def)" in blk
    assert ".ant-radio-wrapper" in blk
    j = ts.index("async function optionLabels(")
    blk2 = ts[j:j + 700]
    assert "def?: any" in blk2 and "scopeOf(modal, def)" in blk2
    # 调用点带字段定义
    assert "optionLabels(modal, check.field, fieldDefs[check.field])" in ts


def test_reconcile_counts_default_covered_assertions():
    """rev167：defaults 带 owners 的 default 断言进恒等式（TR-784 unexplained_plan=4）。"""
    ts = _ts()
    assert "default_covered_assertions" in ts
    assert "const __defaultCovA = FORM_PLAN.default_covered_assertions || 0;" in ts
    k = ts.index("const __unexplainedPlan")
    blk = ts[k:k + 200]
    assert "+ __defaultCovA" in blk
    assert "default_covered: __defaultCovA" in ts
    src = _py()
    assert '"default_covered_assertions": len(covered_defaults)' in src


def test_payload_whitelist_carries_default_covered_and_choice_reject():
    """rev167 补丁：render_form_executor 白名单漏 default_covered_assertions 的后果
    与 rev131/TR-748 同型（TR-785 default_covered 恒 0）；choice_options 的 reject
    分支与 radio_options 对齐（非法值不存在 = pass）。"""
    src = _py()
    assert '"default_covered_assertions": plan.get("default_covered_assertions") or 0,' in src
    ts = _ts()
    i = ts.index("if (check.type === 'choice_options')")
    blk = ts[i:i + 2400]
    assert "startsWith('reject')" in blk
    assert "竟出现在勾选组里" in blk
    assert "用户无法勾选非法值" in blk


def test_digest_warmed_at_import():
    """P4 窄口径（rev169）+ rev170 修正：import 期预热 digest——但**必须放在模块末尾**。

    rev169 把预热放在模块体前部（第 ~450 行），执行时 `_FORM_EXECUTOR_TS` /
    `render_form_executor` / 多数 digest 函数尚未定义，`g.get` 静默跳过 ⇒
    lru_cache 固化**不含执行器模板的残缺 digest**（实测 warm=fec94279864a ≠
    full=ad10a9c44913，rev169 登记的就是残缺值）。回归：预热调用必须在
    `render_form_executor` 定义之后；且 import 后的 digest 与 cache_clear 重算一致。
    """
    src = _py()
    assert "compiler_source_digest()" in src
    # 旧位置（模块体前部）不得再出现裸调用
    head = src[:src.index("def render_form_executor")]
    assert head.count("\ncompiler_source_digest()") == 0
    # 调用在文件末尾（render_form_executor 定义之后）
    tail = src[src.index("def render_form_executor"):]
    assert "\ncompiler_source_digest()" in tail
    import app.services.web_case_compiler as m
    warmed = m.compiler_source_digest()
    m.compiler_source_digest.cache_clear()
    assert warmed == m.compiler_source_digest(), "预热 digest ≠ 全量 digest（预热位置过早）"


# ---------- rev170：filter_checks 筛选区通道 ----------

def test_filter_checks_parse_fields_and_defaults():
    items, rejects = parse_filter_checks([
        {"option": "启用", "option_labels": ["启用", "啟用"], "reset": True,
         "list_url": "https://x/list", "case_id": "TC-F1"},
    ])
    assert not rejects and len(items) == 1
    it = items[0]
    assert it["option"] == "启用" and it["option_labels"] == ["启用", "啟用"]
    assert it["expect_column"] == "状态" and it["expect_in"] == ["启用"]  # 缺省 = [option]
    assert it["control_placeholder"] == ["狀態", "状态", "Status"]
    assert it["reset"] is True and "重\\s*置" in it["reset_label"]
    assert it["name"] == "filter:启用@状态" and it["rule_ref"] == "筛选与重置"


def test_filter_checks_rejects_missing_option():
    items, rejects = parse_filter_checks(["bad", {"reset": True}, {"option": " "}, {"option": "启用"}])
    assert len(items) == 1 and items[0]["option"] == "启用"
    assert len(rejects) >= 3  # 非对象 + 缺 option ×2
    assert all(r.get("reason") for r in rejects)
    # 空配置整体不产生任何东西
    assert parse_filter_checks(None) == ([], [])


def test_filter_checks_reaches_form_plan():
    """parse 产出 filter_check 但 build_form_plan 不透传 ⇒ 执行器拿不到（TR-776 同型）。"""
    src = _py()
    i = src.index('"type": "filter_check"')
    blk = src[i:i + 900]
    for key in ("control_placeholder", "option_labels", "expect_column", "expect_in",
                "reset", "reset_label"):
        assert f'"{key}": item["{key}"]' in blk
    # 早退分支 + 主分支都要带被拒清单
    assert src.count('"filter_check_rejects": filter_check_rejects') >= 2


def test_filter_checks_whitelist_and_phase_accounting():
    """rev170：filter_check_rejects 必须进 payload 白名单（TR-748 同型教训）；
    filter_check 不进字段级主循环、有独立阶段与 summary 行。"""
    src = _py()
    assert '"filter_check_rejects": plan.get("filter_check_rejects") or [],' in src
    assert '"filter_checks"' in src  # NON_LOCATOR_CONFIG_KEYS
    ts = _ts()
    assert "check.type === 'filter_check'" in ts
    assert "async function runFilterChecks()" in ts
    assert "FILTER_PHASE" in ts
    assert "[filter-skip]" in ts
    assert "rejected_at_plan: (FORM_PLAN.filter_check_rejects || []).length" in ts


def test_filter_checks_option_exact_before_contains():
    """「启用」是「待启用」的子串：选项匹配必须先精确后包含，否则会选错。"""
    ts = _ts()
    i = ts.index("for (const mode of ['exact', 'contains'])")
    assert ts.index("itemTexts[i] === ol") < ts.index("itemTexts[i].indexOf(ol) >= 0")
    assert "FILTER_OPTIONS" in ts


def test_filter_checks_honest_judgement_bar():
    """0 行/列未命中/结果集不齐 → unverified，不冒充通过；重置必须回读清空态。"""
    ts = _ts()
    assert "筛选后第 1 页无可见行" in ts
    assert "目标列取值不在期望集" in ts
    assert "placeholderVisible" in ts
    assert "filter_ok: filterOk" in ts


def test_filter_checks_in_digest():
    """parse_filter_checks 的解析语义决定运行期选哪个选项 ⇒ 必须纳入 digest（rev125 同源）。"""
    src = _py()
    assert '"parse_filter_checks",' in src
