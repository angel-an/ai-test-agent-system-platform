# -*- coding: utf-8 -*-
"""rev202：導入任務列表子菜单 + 子菜单候选可由配置覆盖。

背景（TC-298680「列表-導入任務列表與下載導入結果」）：
该用例的业务文本不含商品/支付关键词，`_yl_masterdata_child_menu` 会落进**默认分支**
⇒ 点 ``Shop Management``，于是在**店铺列表**上验「創建時間」排序。店铺页恰好也有
``Create Time`` 列，断言有可能通过——但验的是错页面，属**假绿**，比失败更危险。

本文件守三件事：
1. 「導入任務」业务文本解析出**独立**子菜单分支（不再落店铺）；
2. 该分支不与商品/支付分支互相遮蔽（顺序敏感，先命中先返回）；
3. ``field_locators.child_labels`` 可覆盖候选：配置项排在前、与路由候选去重保序
   ——菜单名是被测系统事实，改名/语言切换应改数据而不是改代码。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.web_case_link_service import WebCaseLinkService

#: 不需要依赖注入：这两个方法只用到静态归一化 + 字符串模板，不碰 DB/网络。
SVC = WebCaseLinkService.__new__(WebCaseLinkService)


def _case(name: str, module: str = "") -> SimpleNamespace:
    return SimpleNamespace(name=name, module=module, description=None, identifier="TC-298680")


def _gen(case: SimpleNamespace, override=None) -> str:
    return SVC._generate_yl_masterdata_script(
        case, "https://console.uat.eatizen.com.hk", ["Master Data Management"],
        child_labels_override=override,
    )


def test_import_task_gets_its_own_child_menu_branch():
    """「導入任務」不得落到默认的店铺分支。"""
    label, candidates = SVC._yl_masterdata_child_menu(
        _case("列表-導入任務列表與下載導入結果", "YL-主數據管理-通用")
    )
    assert label == "導入任務列表"
    assert candidates[0] == "Import Task"
    assert "Shop Management" not in candidates


def test_import_branch_does_not_shadow_goods_or_payment():
    """顺序敏感：商品/支付用例仍走各自分支。"""
    goods_label, goods = SVC._yl_masterdata_child_menu(
        _case("列表顯示-商品-欄位完整性校驗", "商品主數據")
    )
    pay_label, _pay = SVC._yl_masterdata_child_menu(
        _case("列表顯示-支付方式-欄位完整性校驗", "支付方式主數據")
    )
    assert goods_label == "商品主數據" and goods[0] == "Item Manage"
    assert pay_label == "支付方式主數據"


def test_child_labels_override_is_prepended():
    """配置候选排在前：UAT 菜单真名与路由推断不一致时，配置说了算。"""
    script = _gen(
        _case("列表-導入任務列表與下載導入結果", "YL-主數據管理-通用"),
        override=["Import Job History"],
    )
    assert 'const childLabels = ["Import Job History", "Import Task"' in script


def test_child_labels_override_still_falls_back_to_route_candidates():
    """配置只是**前置**，不是替换：配置漏了真名时路由候选仍能兜住。"""
    script = _gen(
        _case("列表-導入任務列表與下載導入結果", "YL-主數據管理-通用"),
        override=["Import Job History"],
    )
    body = script.split("const childLabels = ", 1)[1].split(";", 1)[0]
    assert "Import Tasks" in body  # 路由分支里的兜底候选还在


def test_child_labels_override_deduplicates_and_keeps_order():
    """配置里重复写、或与路由候选撞名，都只能出现一次，且保持前置顺序。"""
    script = _gen(
        _case("列表-導入任務列表與下載導入結果", "YL-主數據管理-通用"),
        override=["Import Task", "Import Task", "Import Management"],
    )
    body = script.split("const childLabels = ", 1)[1].split(";", 1)[0]
    assert body.count('"Import Task"') == 1
    # 前置顺序：Import Task → Import Management → 路由候选
    assert body.index('"Import Task"') < body.index('"Import Management"')
    assert body.index('"Import Management"') < body.index('"Import Tasks"')


def test_blank_override_is_ignored():
    """空串/空白项不能让脚本出现空标签候选（点空文本会点到任意元素）。"""
    script = _gen(
        _case("列表-導入任務列表與下載導入結果", "YL-主數據管理-通用"),
        override=["", "   "],
    )
    body = script.split("const childLabels = ", 1)[1].split(";", 1)[0]
    assert '""' not in body
    assert body.startswith('["Import Task"')


def test_generated_title_carries_business_label():
    """失败时的用例标题要能看出跑的是哪个业务，否则错页面的证据看不懂。"""
    script = _gen(_case("列表-導入任務列表與下載導入結果", "YL-主數據管理-通用"))
    assert "YL導入任務列表主數據管理入口冒烟" in script


# ---------------------------------------------------------------------------
# 列表域**无表单**子功能：定位器解析不得把它判成"没配置"
# ---------------------------------------------------------------------------

SORT_ONLY_MANIFEST = {
    "case_count": 1,
    "cases": [{
        "identifier": "TC-298680",
        "rule_id": "FR-YL-LIST-T",
        "assertions": [{
            "kind": "sort_order", "field": "列表",
            "rule": "任務列表：任務名取文件名/狀態/導入結果條數/創建人/創建時間/下載導入結果；按創建時間近→遠",
            "data": {"column": "創建時間", "direction": "desc", "source_text": "近→遠"},
            "origin": "rule_text",
        }],
    }],
}

LIST_ONLY_LOCATORS = {
    "name": "yl_導入任務_list", "flow": "create_page", "fields": {}, "modal": "body",
    "column_aliases": {"創建時間": "Create Time"},
    "child_labels": ["Import Task"],
}


def test_list_only_locators_count_as_configured():
    """只有 column_aliases/child_labels 的列表页配置，必须算"有配置"。

    TR-823 实测：这一步返回 False ⇒ resolve_form_locators 返回 (None, "none")
    ⇒ build_form_plan 根本没被调用 ⇒ 脚本里 SORT_CHECKS=[] 却整条 passed（假绿）。
    """
    from app.services.web_case_compiler import has_channel_config

    assert has_channel_config(LIST_ONLY_LOCATORS) is True


def test_list_only_locators_resolve_to_sub_function_override():
    from app.services.web_case_compiler import resolve_form_locators

    locators, source = resolve_form_locators("yl_masterdata", LIST_ONLY_LOCATORS, None)
    assert source == "sub_function_override"
    assert locators is LIST_ONLY_LOCATORS


def test_list_only_plan_carries_sort_check_with_alias():
    """端到端：规则排序断言 + 列名别名 ⇒ 计划里必须有 sort_checks（否则等于没验）。"""
    from app.services.web_case_compiler import build_form_plan, resolve_form_locators

    locators, _ = resolve_form_locators("yl_masterdata", LIST_ONLY_LOCATORS, None)
    plan = build_form_plan(SORT_ONLY_MANIFEST, locators)
    assert plan is not None, "列表页配置被判成没配置：排序断言会被静默丢弃"
    sort_checks = (plan.get("locators") or {}).get("sort_checks") or []
    assert len(sort_checks) == 1
    assert sort_checks[0]["column"] == "創建時間"
    assert sort_checks[0]["aliases"] == ["Create Time"]

