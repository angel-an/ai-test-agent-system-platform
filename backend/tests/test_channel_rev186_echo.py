# -*- coding: utf-8 -*-
"""rev186：配置保真·回显一致性独立通道（echo_checks）。

与 rev170 filter_checks 先例同形的验证面：
1. parse_echo_checks 语义（fields 必填 / kind 域 / pick 缺省 / required_fields 归一）
2. 通道登记完整性（digest / CHANNEL_ONLY / NON_LOCATOR / 计划两分支 / 白名单 /
   TS 阶段函数 / 看门狗 / summary 对账 / [echo-skip] 不静默）
3. 升轨逻辑（service 侧 plan_has_echo → persistence_echo_case 翻 auto）
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services.web_case_compiler import (  # noqa: E402
    CHANNEL_ONLY_CONFIG_KEYS,
    COMPILER_REV,
    NON_LOCATOR_CONFIG_KEYS,
    build_form_plan,
    parse_echo_checks,
)


def _py() -> str:
    return (BACKEND / "app" / "services" / "web_case_compiler.py").read_text(encoding="utf-8")


def _svc() -> str:
    return (BACKEND / "app" / "services" / "web_case_link_service.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- parse 语义
class TestParseEchoChecks:
    def test_valid_config_normalized(self):
        items, rejects = parse_echo_checks([{
            "list_url": "#/survey/list",
            "fields": [
                {"label": "適用門店", "kind": "multi_select", "pick": 2},
                {"label": "適用商品", "kind": "select"},
            ],
            "required_fields": [{"label": "标题", "value": "ECHO-x"}],
        }])
        assert not rejects
        assert len(items) == 1
        it = items[0]
        assert it["name"] == "echo:適用門店"          # 缺省名 = echo:<首字段>
        assert it["fields"][0]["pick"] == 2           # 多选缺省 2
        assert it["fields"][1]["pick"] == 1           # 单选缺省 1
        assert it["fields"][0]["option_labels"] == []
        assert it["required_fields"][0]["kind"] == "input"
        assert it["rule_ref"] == "配置保真与回显"
        assert it["new_button"] and it["save_button"]

    def test_missing_fields_rejected(self):
        items, rejects = parse_echo_checks([{"list_url": "#/x"}, "not-a-dict", None])
        assert not items
        assert len(rejects) == 3
        assert "缺少 fields" in rejects[0]["reason"]

    def test_field_without_label_rejected(self):
        items, rejects = parse_echo_checks([{"fields": [{"kind": "select"}]}])
        assert not items
        assert any("缺少 label" in r["reason"] for r in rejects)

    def test_invalid_kind_rejected(self):
        items, rejects = parse_echo_checks([{"fields": [{"label": "门店", "kind": "color"}]}])
        assert not items
        assert any("kind 取值非法" in r["reason"] for r in rejects)

    def test_input_kind_pick_forced_zero(self):
        items, _ = parse_echo_checks([{"fields": [{"label": "编码", "kind": "input", "pick": 3}]}])
        assert items[0]["fields"][0]["pick"] == 0

    def test_bad_pick_falls_back(self):
        items, _ = parse_echo_checks([{"fields": [{"label": "门店", "kind": "multi_select", "pick": "x"}]}])
        assert items[0]["fields"][0]["pick"] == 2

    def test_empty_raw_no_items(self):
        for raw in (None, "", []):
            items, rejects = parse_echo_checks(raw)
            assert items == [] and rejects == []

    def test_non_list_rejected(self):
        items, rejects = parse_echo_checks({"fields": []})
        assert not items
        assert "必须是数组" in rejects[0]["reason"]


# ---------------------------------------------------------------- 通道登记
class TestEchoChannelRegistration:
    def test_channel_keys_registered(self):
        assert "echo_checks" in CHANNEL_ONLY_CONFIG_KEYS
        assert "echo_checks" in NON_LOCATOR_CONFIG_KEYS

    def test_digest_function_registered(self):
        src = _py()
        assert '"parse_echo_checks"' in src

    def test_plan_rejects_in_both_return_branches(self):
        src = _py()
        # 早退分支与主分支都要带 echo_check_rejects（rev170 的教训：缺一会被测试抓住）
        assert src.count('"echo_check_rejects": echo_check_rejects,') == 2

    def test_render_whitelist_registered(self):
        src = _py()
        assert '"echo_check_rejects": plan.get("echo_check_rejects") or []' in src

    def test_ts_list_domain_kinds_and_loop_skip(self):
        src = _py()
        assert "'echo_check'" in src.split("LIST_DOMAIN_KINDS =")[1].split(";")[0]
        assert "check.type === 'echo_check') continue;" in src

    def test_ts_runtime_phase_wired(self):
        src = _py()
        assert "async function runEchoChecks" in src
        assert "phaseWatchdog(runEchoChecks(), 240000, 'echo')" in src
        assert "ECHO_PHASE" in src and "ECHO_TRACE" in src
        assert "'[echo-skip] '" in src.replace("console.log(", "'").replace(
            " + JSON.stringify(rej));", "'")
        assert "'echo_phase':" in src.replace(" ", "").replace("\n", "") or "echo_phase:" in src

    def test_summary_phase_has_rejected_at_plan(self):
        src = _py()
        seg = src.split("echo_phase: {")[1].split("},")[0]
        assert "rejected_at_plan" in seg
        assert "echo_check_rejects" in seg

    def test_compiler_rev_bumped(self):
        assert COMPILER_REV == "rev186"


# ---------------------------------------------------------------- 升轨
class TestEchoPromotion:
    def test_service_flip_logic_present(self):
        src = _svc()
        assert "plan_has_echo" in src
        assert 'persistence_echo_case' in src
        assert "rev186 echo_check 通道覆盖" in src

    def test_bind_call_site_passes_form_plan(self):
        src = _svc()
        assert "form_plan=form_plan," in src


# ---------------------------------------------------------------- 计划消费
class TestEchoCheckReachesPlan:
    def test_echo_check_reaches_form_plan(self):
        """配置了 echo_checks 的定位器表 → 计划含 echo_check 且 rejected 进 plan 键。"""
        loc = {
            "list_url": "#/x/list",
            "echo_checks": [{
                "fields": [{"label": "適用門店", "kind": "multi_select"}],
                "required_fields": [{"label": "名稱", "value": "ECHO-{{RUN_TAG}}"}],
            }],
        }
        plan = build_form_plan({}, loc)
        assert plan is not None, "只有通道配置、无 fields 也必须生成计划（CHANNEL_ONLY 语义）"
        types = [c.get("type") for c in plan.get("checks", [])]
        assert "echo_check" in types
        echo = next(c for c in plan["checks"] if c["type"] == "echo_check")
        assert echo["list_url"] == "#/x/list"
        assert echo["fields"][0]["label"] == "適用門店"
        assert echo["fields"][0]["pick"] == 2
        assert plan["executable"] is True
        assert plan["echo_check_rejects"] == []

    def test_echo_check_bad_config_lands_in_rejects(self):
        loc = {"list_url": "#/x/list", "echo_checks": [{"fields": []}]}
        plan = build_form_plan({}, loc)
        assert plan is not None
        assert plan["echo_check_rejects"], "被拒配置必须进 plan 键（[echo-skip] 不静默）"
        assert not [c for c in plan.get("checks", []) if c.get("type") == "echo_check"]
