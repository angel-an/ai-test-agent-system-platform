# -*- coding: utf-8 -*-
"""purchase_limit Web 模板键：识别词、模板分派、生成产物结构（P1）"""
from types import SimpleNamespace

import pytest

from app.services.web_case_link_service import (
    WebCaseLinkService,
    _PURCHASE_LIMIT_KEYWORDS,
    _WEB_TEMPLATE_LABELS,
)


def _case(name="", module="", description="", identifier="TC-T"):
    return SimpleNamespace(
        identifier=identifier, name=name, module=module, description=description,
    )


class TestPurchaseLimitRecognition:
    def test_label_registered(self):
        assert _WEB_TEMPLATE_LABELS["purchase_limit"] == "B端会员限购商品管理"

    @pytest.mark.parametrize("text", [
        "限购商品名称输入20字可正常保存（上边界）",   # F 组字段规则
        "会员限购商品管理列表完整展示五个业务字段",   # B 组列表
        "限购商品可以设置金额条件进行加购",      # 需求背景场景
        "消费金额门槛输入9999可正常保存",       # 口径裁定用例
    ])
    def test_positive_match(self, text):
        svc = WebCaseLinkService.__new__(WebCaseLinkService)
        assert svc._is_purchase_limit_case(_case(name=text)) is True
        assert svc._resolve_web_template_key(_case(name=text)) == "purchase_limit"

    @pytest.mark.parametrize("text", [
        "B端企业储值充值-储值账户列表冒烟",
        "储值免单活动创建",
        "CS工单管理列表验证",
        "开屏广告素材配置检查",
        "订单退款流程验证",
    ])
    def test_no_false_positive(self, text):
        svc = WebCaseLinkService.__new__(WebCaseLinkService)
        assert svc._is_purchase_limit_case(_case(name=text, module=text)) is False

    def test_module_alone_matches(self):
        # 补齐用例模块名即命中（与 PR-2 限购用例实际形态一致）
        svc = WebCaseLinkService.__new__(WebCaseLinkService)
        assert svc._resolve_web_template_key(
            _case(name="正向流程", module="限购商品管理(加购商品限制)-字段规则")
        ) == "purchase_limit"

    def test_unknown_business_still_rejected(self):
        svc = WebCaseLinkService.__new__(WebCaseLinkService)
        with pytest.raises(ValueError, match="unsupported_web_template"):
            svc._resolve_web_template_key(_case(name="优惠券模板列表检查", module="优惠券"))


class TestPurchaseLimitScriptGeneration:
    def test_generated_script_structure(self):
        svc = WebCaseLinkService.__new__(WebCaseLinkService)
        script = svc._generate_purchase_limit_script(
            _case(identifier="TC-133292", name="限购商品名称输入20字"),
            "https://console-uat.xysjg.com/front/dtcloud-console-web-pc/#/login",
            ["企业控制台登录", "门户中心", "点击数盈交易云卡片进入交易云", "点击运营菜单", "点击会员限购商品管理菜单", "进入会员限购商品管理列表页"],
            manifest_block="// MANIFEST",
            coverage_snippet="// COVERAGE",
            form_executor="// FORM_EXECUTOR",
        )
        # 交易云门户与导航
        assert "数盈·交易云" in script and "Trading cloud" in script
        assert "NAV_STEPS" in script
        # 门户段/卡片段被过滤，应用内菜单保留且顺序不变
        nav_block = script.split("NAV_STEPS =")[1].split("];")[0]
        assert '"运营"' in nav_block
        assert '"会员限购商品管理"' in nav_block
        assert "门户中心" not in nav_block
        assert "数盈交易云" not in nav_block
        assert "点击数盈交易云卡片进入交易云" not in nav_block
        # 列表五列断言（R33）+ 新建入口
        for col in ("限购商品名称", "限购标签", "限购周期", "状态"):
            assert f"'{col}'" in script
        assert "/新\\s*建/" in script
        # 只读探针声明 + 可插拔件落位
        assert "本版不提交" in script
        assert "// MANIFEST" in script and "// COVERAGE" in script and "// FORM_EXECUTOR" in script
        # 环境阻断前置判定（与储值模板一致的防误判机制）
        assert "ENVIRONMENT_BLOCKED" in script
        # 弹窗兜底：定位器点击未带出弹窗时降级真实鼠标点击卡片中心
        assert "page.mouse.click" in script
        # 外壳判定不含"设置"（门户用户菜单也含"设置"，会误判）
        assert "门店|产品|订单|运营" in script
        shell_line = next(l for l in script.splitlines() if "document.body.innerText" in l and "门店" in l)
        assert "设置" not in shell_line
        # 动作描述归一化为菜单元素文本（"点击运营菜单" → "运营"）
        nav_decl = script.split("NAV_STEPS =")[1].split("];")[0]
        assert '"运营"' in nav_decl
        assert '"会员限购商品管理"' in nav_decl
        assert "点击" not in nav_decl and "菜单" not in nav_decl

    def test_empty_navigation_path_still_generates(self):
        svc = WebCaseLinkService.__new__(WebCaseLinkService)
        script = svc._generate_purchase_limit_script(
            _case(identifier="TC-X", name="限购商品"),
            "https://example.com/login",
            [],
        )
        assert "NAV_STEPS = []" in script
