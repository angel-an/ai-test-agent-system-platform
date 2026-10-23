# -*- coding: utf-8 -*-
"""rev140：错误检测锚定控件 + 提交层时间线/限界（静态模板断言）。

背景（TR-756 实测）：required_error:题目标题/选项1 卡 SUBMIT_TIMEOUT settle_done 90s。
探针三事实：卡片标签是「标题」≠ 字段名；卡片在 .ant-form（modal 配置）之外；
错误形态是 antd v3（.ant-form-explain + control.has-error）。
"""
import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "app" / "services" / "web_case_compiler.py"


def _ts() -> str:
    import app.services.web_case_compiler as m  # noqa: E402
    return m._FORM_EXECUTOR_TS


def _py() -> str:
    return SRC.read_text(encoding="utf-8")


def test_rev_bumped():
    src = _py()
    match = re.search(r'COMPILER_REV = "rev(\d+)"', src)
    assert match, "COMPILER_REV 定义丢失"
    # 只要求「不早于引入本测试的那一版」，不钉死具体版本号。
    # 历史教训：这里曾被追成字面量 ``"rev180"``，此后每 bump 一次就误红一次，
    # 从 rev181 一路红到 rev196（无人跑门禁集，红线被埋了 16 个版本）。
    assert int(match.group(1)) >= 140
    ast.parse(src)  # 语法完好


def test_anchored_error_detection_exists():
    ts = _ts()
    assert "async function itemHasErrorCtl" in ts
    # 主路：按字段定位路径找控件 → 向上找最近 .ant-form-item（**精确 token** 匹配，
    # 旧 contains 写法会先命中 div.ant-form-item-control —— 探针3 实锤）
    assert 'ancestor::div[contains(concat(" ", normalize-space(@class), " "), " ant-form-item ")][1]' in ts
    assert 'ancestor::div[contains(@class,"ant-form-item")][1]' not in ts
    # antd v3 与 v4 两种错误形态都要查
    assert ".ant-form-explain" in ts
    assert ".ant-form-item-explain-error" in ts
    # 锚定失败退回旧的标签匹配（0 等待，避免双重等待）
    assert "return await itemHasError(pg, modal, field, 0);" in ts


def test_client_layer_uses_anchored_detection():
    ts = _ts()
    # required_error 客户端层：rev165 起先按「时机」读锚定文案（itemErrorTexts 与
    # itemHasErrorCtl 同一套 ancestor 锚定），只有全无文案时才退回布尔检测兜底。
    assert "async function itemErrorTexts(" in ts
    assert "const errBase = await itemErrorTexts(modal, check.field, def);" in ts
    assert "if (timing === 'none' && await itemHasErrorCtl(page, modal, check.field, def, 0)) timing = 'flag';" in ts
    # input_validation 边界检测
    assert "const invalid = await itemHasErrorCtl(page, modal, check.field, def);" in ts
    # 旧的裸 itemHasError 客户端调用不再出现（提交层 observe/guardOk 也应换掉）
    assert "await itemHasError(page, modal, check.field)" not in ts
    assert "itemHasError(page, curModal, check.field, 250)" not in ts


def test_submit_layer_timeline_and_bounds():
    ts = _ts()
    # SUBMIT_SETTLE 时间线：命中与未命中都要打点
    assert "SUBMIT_SETTLE" in ts
    assert "hit: false" in ts
    # late 观察窗限界（不再吃满单项预算）
    assert "lateT0" in ts
    assert "SUBMIT_LATE" in ts
    # 前端拦截收敛防线：无业务写请求 + 错误面较基线扩大
    assert "formErrBase" in ts and "formErrCount" in ts
    assert "__frontendBlocked" in ts
    # pass 文案如实写明判定依据
    assert "未发出业务写请求（前端拦截）" in ts


def test_guardok_and_observe_switched():
    ts = _ts()
    # rev144：guard 的 required_empty 分支放行（原 itemHasErrorCtl 判早退是死代码），
    # observe 的 fieldError 检测仍走锚定版本
    assert "fieldError: await itemHasErrorCtl(page, curModal, check.field, itemDef, 250)," in ts


def test_confirm_click_tightened():
    """确认层点击：先 count 再 isVisible，click 超时 1500ms（旧 4000ms 是 73s 卡死的放大器）。"""
    ts = _ts()
    assert "await cb.click({timeout: 1500}).catch(() => undefined);" in ts
    assert "await cb.click({timeout: 4000}).catch(() => undefined);" not in ts


def test_fill_empty_errored_handles_radio_groups():
    """rev141：兜底填充必须真实点击 radio/checkbox 组（探针3：radio 组未注册 →
    保存被拦在外层表单，卡片校验永不执行）。"""
    ts = _ts()
    assert "input[type=radio], input[type=checkbox]" in ts
    assert ".ant-radio-wrapper:visible, .ant-checkbox-wrapper:visible" in ts
    assert "await rcInput.check({timeout: 1500}).catch(() => undefined);" in ts


def test_frontend_blocked_has_attribution_branch():
    """rev141：收敛信号只证明「提交被拦」，必须归因到目标字段才 pass。"""
    ts = _ts()
    assert "} else if (late && (late as any).__frontendBlocked) {" in ts
    assert "证据命中目标字段" in ts
    assert "但均不属于目标字段" in ts


def test_synthetic_click_guards_empty_locator():
    """rev141：空定位器上的 .evaluate 白等 30s —— 先 count 再 evaluate。"""
    ts = _ts()
    seg = ts[ts.index("if (i === 15 && !synthetic)"):ts.index("if (i === 15 && !synthetic)") + 500]
    assert ".count().catch(() => 0)" in seg
    assert seg.index(".count()") < seg.index(".evaluate(")


def test_toast_text_matching_and_alias_attribution():
    """rev142：必填拦截的真实形态是 warning 样式 toast（探针4 截图实锤），
    必须按文本匹配任意通知 + 用别名反查归因。"""
    ts = _ts()
    assert "blockToastText" in ts
    assert "TOAST_BLOCK_RE" in ts
    # 文本匹配，不依赖 error 样式类
    assert "请输入|请选择|必填|不能为空|请填写" in ts
    assert "'.ant-message-notice, .ant-notification-notice'" in ts
    # 归因表：field_alias_applied 反转 + attrHit 查字段名与别名
    assert "ALIAS_SOURCES" in ts and "attrHit" in ts
    assert "FORM_PLAN.field_alias_applied" in ts
    # 判定分支：归因命中才 pass，否则 unverified + 地面真相行
    assert "命中目标字段" in ts
    assert "但未命中目标字段" in ts
    assert "SUBMIT_ERR_DETAIL" in ts
    # 稳定无信号早停
    assert "stableRounds" in ts


def test_errored_item_covers_v3():
    """rev143：ERRORED_ITEM 必须覆盖 antd v3（.has-error 在 control 上、explain 是
    .ant-form-explain）——否则 fillEmptyErrored 空转（TR-759 实锤）。"""
    ts = _ts()
    assert ":has(.ant-form-explain)" in ts
    assert ":has(.has-error)" in ts
    assert ":has(.ant-form-item-explain-error)" in ts
    # toast 快查：存续 ~3s，必须先于 ~2.5s 的全量观察
    assert "btFast" in ts


def test_required_empty_guard_released():
    """rev144：required_empty 的 guardOk 恒 false 使 fillEmptyErrored 成为死代码
    （TR-760 实锤），必须放行（probe 守卫不变）。"""
    ts = _ts()
    seg = ts[ts.index("let guardOk = true;"):ts.index("if (!guardOk) return await observe();")]
    assert seg.index("item.kind === 'probe'") >= 0
    # required_empty 分支不再做锚定检测判早退
    assert "itemHasErrorCtl" not in seg.split("} else {")[1]
    assert "guardOk = true;" in seg.split("} else {")[1]


def test_multi_toast_join_and_unchecked_first():
    """rev144：blockToastText 拼接全部命中（一保存多条 toast）；checkbox 组点未选中项。"""
    ts = _ts()
    assert ".filter((t) => TOAST_BLOCK_RE.test(t)).join(' | ')" in ts
    assert "isChecked()" in ts


def test_shared_fill_and_block_siblings():
    """rev145：填充逻辑共享化（场景层复用）+ block_siblings + label 点击后验证。"""
    ts = _ts()
    assert "async function fillErroredItems(container: any, skipMarked: boolean): Promise<number>" in ts
    assert "await fillErroredItems(curModal, true);" in ts
    assert "block_siblings" in ts
    # label 点击后验证：未选中则合成 click 兜底
    assert "rcV.isChecked()" in ts
    assert "await rcV.evaluate((el: any) => (el as any).click())" in ts


def test_payload_carries_alias_map():
    """rev142：渲染载荷必须下发 field_alias_applied（运行期要读的计划字段须入白名单）。"""
    src = _py()
    assert '"field_alias_applied": plan.get("field_alias_applied") or [],' in src


def test_digest_covers_executor_template():
    """模板改动必须被 digest 覆盖（防指纹漂移）。"""
    import app.services.web_case_compiler as m  # noqa: E402
    src = SRC.read_text(encoding="utf-8")
    for name in ("_FORM_EXECUTOR_TS",):
        assert name in src
    assert "itemHasErrorCtl" in m._FORM_EXECUTOR_TS
