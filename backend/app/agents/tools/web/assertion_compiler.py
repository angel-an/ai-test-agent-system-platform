"""Web 断言编译（评审路线图第 3 步：先断言）。

目标：expected_result（自然语言）在保存时**编译为可机检的结构化断言**；
编译不出可执行断言的标 **human_oracle**（人工判定），执行时**只校验**
compiled_assertions，绝不现场发明断言。

设计（确定性规则，非 LLM）：
- 能映射为明确可检查谓词的 → compiled（结构化断言条目）；
- 纯模糊/无具体对象的表述（如单独"成功/正确/正常/验证通过"）→ human_oracle，
  不进入自动校验范围（与 agent prompt 的模糊词禁令一致）。

断言条目结构：
    {"type": "status", "expected": "成功"}            # 结果状态类
    {"type": "contains", "target": "xx"}              # 页面/响应包含目标
    {"type": "numeric", "key": "余额", "op": "=", "value": 123.45}
    {"type": "field", "key": "状态", "op": "=", "value": "已发货"}
"""

from __future__ import annotations

import re
from typing import Any

# 模糊词：单独出现无具体对象 → human_oracle（agent prompt 亦禁止）
_VAGUE_WORDS = ("成功", "正确", "正常", "通过", "验证通过", "无异常", "符合预期")

_STATUS_WORDS = ("成功", "失败", "完成", "已生效", "已保存", "已创建", "已删除",
                 "已更新", "已取消", "无记录", "为空", "空列表")

# "XX(为|是|等于|变成|=) 值" 或 "XX 包含/出现/显示 YY"
_FIELD_RE = re.compile(r"^(.{1,12})(?:为|是|等于|变成|=)\s*(.+)$")
_CONTAINS_RE = re.compile(r"(?:包含|出现|存在|显示|可见|展示)\s*(.{1,30})")
_NUMERIC_RE = re.compile(r"^(.{1,12})(?:为|是|等于|=)\s*(\d+(?:\.\d+)?)$")


def _compile_one(text: str) -> tuple[dict | None, bool]:
    """编译单条 expected_result。返回 (assertion 或 None, 是否可机检)。

    (None, False) → human_oracle（无法编译为可执行断言）。
    """
    t = (text or "").strip()
    if not t:
        return None, False
    # 纯模糊词 → human_oracle
    if t in _VAGUE_WORDS:
        return None, False
    # 数值型：余额/数量 = 数字
    m = _NUMERIC_RE.match(t)
    if m:
        key = m.group(1).strip()
        try:
            value = float(m.group(2))
        except ValueError:
            value = m.group(2)
        return {"type": "numeric", "key": key, "op": "=", "value": value}, True
    # 字段断言：X为Y（Y 具体）
    m = _FIELD_RE.match(t)
    if m:
        key = m.group(1).strip()
        value = m.group(2).strip()
        if value and not _is_vague_value(value):
            return {"type": "field", "key": key, "op": "=", "value": value}, True
    # 包含断言
    m = _CONTAINS_RE.search(t)
    if m:
        target = m.group(1).strip()
        if target and not _is_vague_value(target):
            return {"type": "contains", "target": target}, True
    # 状态类词（且文本非纯模糊、带对象信息 → status 断言兜底）
    if any(w in t for w in _STATUS_WORDS) and len(t) >= 4:
        return {"type": "status", "expected": t[:50]}, True
    return None, False


def _is_vague_value(value: str) -> bool:
    """值本身模糊（成功/正常 等）→ 不编译为字段值断言。"""
    return any(w in value for w in _VAGUE_WORDS) and len(value) <= 8


def compile_expected_results(
    expected_results: list[str] | str | None,
) -> dict[str, Any]:
    """编译 expected_results → {assertions, mode, human_oracle, uncompiled}。

    mode: "compiled"（全部可机检）/ "human_oracle"（全部无法编译）/
          "mixed"（部分可机检、部分人工）。
    """
    items: list[str] = []
    if isinstance(expected_results, str):
        items = [s.strip() for s in expected_results.splitlines() if s.strip()]
    elif isinstance(expected_results, list):
        items = [str(s).strip() for s in expected_results if str(s).strip()]
    elif expected_results is None:
        items = []

    assertions: list[dict] = []
    uncompiled: list[str] = []
    for it in items:
        # 单条可能是"1. xxx"编号形式
        body = re.sub(r"^\d+[.、)]\s*", "", it).strip()
        if not body:
            continue
        assertion, machine = _compile_one(body)
        if machine and assertion is not None:
            assertions.append(assertion)
        else:
            uncompiled.append(body)
    if assertions and uncompiled:
        mode = "mixed"
    elif assertions:
        mode = "compiled"
    elif items:
        mode = "human_oracle"
    else:
        mode = "human_oracle"
    return {
        "assertions": assertions,
        "mode": mode,
        "human_oracle": uncompiled,
    }


def build_evidence_from_self_reflect(sr_data: dict | None) -> dict[str, Any]:
    """从 self_reflect_result.json 构建断言评估证据（rev58 执行证据闭环）。

    契约：脚本可在 self_reflect_result.json 写入结构化证据——
      {"execution_status": "passed",
       "evidence": {"status_text": "保存成功", "page_text": "...页面文本...",
                    "fields": {"订单状态": "已发货", "余额": "735.51"}}}
    兼容旧产物：无 evidence 时退化——
      status_text ← execution_status（passed→成功/failed→失败映射）
      page_text  ← steps[].detail 拼接（webwright 旧格式）。
    """
    if not isinstance(sr_data, dict):
        return {}
    evidence = sr_data.get("evidence")
    if isinstance(evidence, dict):
        out = dict(evidence)
        if not out.get("status_text"):
            out["status_text"] = _status_text_from_execution(sr_data.get("execution_status"))
        return out
    # 退化构建
    status_text = _status_text_from_execution(sr_data.get("execution_status"))
    page_text = ""
    steps = sr_data.get("steps")
    if isinstance(steps, list):
        details = [str(s.get("detail", "")) for s in steps
                   if isinstance(s, dict) and s.get("detail")]
        page_text = "\n".join(details)[:20000]
    return {"status_text": status_text, "page_text": page_text, "fields": {}}


def _status_text_from_execution(status) -> str:
    s = str(status or "").lower()
    if s == "passed":
        return "成功"
    if s == "failed":
        return "失败"
    return s


def evaluate_run_assertions(
    assertions: list[dict],
    sr_data: dict | None,
) -> dict[str, Any]:
    """执行后校验（rev58）：证据取自 self_reflect_result.json（脚本自采集或
    webwright 步骤退化）；只校验 compiled_assertions。"""
    evidence = build_evidence_from_self_reflect(sr_data)
    return evaluate_compiled_assertions(assertions, evidence)


def evaluate_compiled_assertions(
    assertions: list[dict],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """执行后校验 compiled_assertions（只校验，不现场发明断言）。

    evidence 由执行链提供（status_text / page_text / fields）。
    """
    results = []
    for a in assertions:
        atype = a.get("type")
        ok = False
        detail = ""
        try:
            if atype == "status":
                status_text = evidence.get("status_text") or evidence.get("page_text") or ""
                ok = str(a.get("expected", "")) in (status_text or "")
                detail = f"状态文本包含预期: {a.get('expected')}"
            elif atype == "contains":
                text = evidence.get("page_text") or evidence.get("response_text") or ""
                ok = str(a.get("target", "")) in (text or "")
                detail = f"文本包含目标: {a.get('target')}"
            elif atype == "numeric":
                fields = evidence.get("fields") or {}
                raw = str(fields.get(a.get("key", ""), ""))
                try:
                    val = float(raw)
                    expected = float(a.get("value", 0))
                    ok = val == expected
                except (TypeError, ValueError):
                    ok = False
                detail = f"字段 {a.get('key')}={raw}（期望 {a.get('value')}）"
            elif atype == "field":
                fields = evidence.get("fields") or {}
                raw = str(fields.get(a.get("key", ""), ""))
                ok = raw == str(a.get("value", ""))
                detail = f"字段 {a.get('key')}={raw}（期望 {a.get('value')}）"
            else:
                ok = False
                detail = f"未知断言类型: {atype}"
        except Exception as e:  # 评估异常不崩溃，记为不通过
            ok = False
            detail = f"评估异常: {e}"
        results.append({"assertion": a, "ok": bool(ok), "detail": detail})
    return {
        "total": len(assertions),
        "passed": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "results": results,
    }
