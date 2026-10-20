# -*- coding: utf-8 -*-
"""N5（2026-10-18）：取帧/菜单失败现场的诊断块。

为什么要有这个测试：TR-517 的日志只有 `[nav] clicked: CRM` + `YL_MENU_DUMP []`，
**没有任何 pages/frames 清单**，于是"取错 frame"与"菜单没加载出来"两种完全不同的
故障无法区分（修法一个是改取帧逻辑，一个是查环境/权限/语言）。

这里守住三件事：
1. 两个 CRM 模板都带 `frameDiag`，且**每个失败分支**都真的调用了它；
2. 取帧决策 `SCOPE_PICK` 有留痕（只取 first() 这件事必须可见）；
3. 渲染后没有残留双花括号（模板是 f-string，手写 JS 最容易被花括号转义坑到）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.web_case_link_service import WebCaseLinkService, render_frame_diag


def _case():
    return SimpleNamespace(
        identifier="TC-FRAME01", name="店铺-新建", module="YL-主數據管理-店鋪",
        feature="店鋪", scenario="", background="", description="",
        custom_fields={}, test_case_steps=[],
    )


def _render(kind: str) -> str:
    svc = WebCaseLinkService.__new__(WebCaseLinkService)
    if kind == "yl":
        return svc._generate_yl_masterdata_script(
            _case(), "https://uat.example", ["CRM", "Master Data Management"],
        )
    return svc._generate_cs_module_script(
        _case(), "https://uat.example", ["CRM", "Customer Service"],
        project_identifier="PR-9",
    )


class TestRenderFrameDiagBlock:
    def test_block_defines_helper_and_lists_frames(self):
        js = render_frame_diag()
        assert "async function frameDiag" in js
        # 必须同时给出 page 清单、frame 清单、iframe src —— 缺一样都判不了因
        assert "context().pages()" in js
        assert "appPage.frames()" in js
        assert "getAttribute('src')" in js
        # 判读口径写在代码里，避免下一个人重新猜
        assert "取错 frame" in js

    @pytest.mark.parametrize("kind", ["yl", "cs"])
    def test_template_contains_helper_once(self, kind):
        script = _render(kind)
        assert script.count("async function frameDiag") == 1

    @pytest.mark.parametrize("kind,labels", [
        ("yl", ("yl-menu-expand", "yl-submenu-click", "yl-list-not-rendered")),
        ("cs", ("cs-menu-expand", "cs-submenu-click", "cs-list-not-rendered")),
    ])
    def test_every_failure_path_emits_frame_diag(self, kind, labels):
        script = _render(kind)
        lines = [line.strip() for line in script.splitlines()]
        for label in labels:
            target = f"FRAME_DIAG ${{await frameDiag(appPage, '{label}')}}"
            # 必须是**真的被打印**：只判子串出现会被注释行骗过
            #（变异验证实测：注释掉调用后，`in script` 仍然通过）。
            assert any(target in line and line.startswith("console.log(") for line in lines), label

    @pytest.mark.parametrize("kind", ["yl", "cs"])
    def test_scope_pick_is_logged(self, kind):
        script = _render(kind)
        assert "SCOPE_PICK" in script
        # 选中 iframe 与退化到 page 两条路径都要留痕
        assert "chosen: 'iframe#0'" in script
        assert "chosen: 'page'" in script

    @pytest.mark.parametrize("kind", ["yl", "cs"])
    def test_no_double_brace_leak(self, kind):
        """f-string 里手写 JS 时，漏写 `{{` 会渲染出语法错误；多写 `{{` 会留下双花括号。"""
        script = _render(kind)
        assert "{{" not in script
        assert "}}" not in script

    @pytest.mark.parametrize("kind", ["yl", "cs"])
    def test_frame_selection_logic_unchanged(self, kind):
        """本轮只补诊断，**不**改取帧策略：仍然是 first()。

        改成多帧遍历要先有诊断数据支撑（复核文档 N5 修法顺序：① 补诊断 ② 再定是否改）。
        """
        script = _render(kind)
        assert "return appPage.frameLocator('iframe').first();" in script
