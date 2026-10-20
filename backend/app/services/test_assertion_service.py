# -*- coding: utf-8 -*-
"""结构化断言的**校验 / 预览 / 回写**（2026-09-29 复核 P2，通用能力）。

背景（限购批次实测）：把"叙述型"用例转成"可编译可执行"，当时是脚本直接 PATCH
``custom_fields.assertions``；平台虽有 ``supplement_case_rule_tool``，但它一次只处理一条、
没有预览、也不记录断言来源。于是批次场景下只能绕过平台写脚本，且**事后无法区分**
"文档抽取得来的断言"与"人工回写的断言"——需求文档（如 99999 口径）回改后，
完全不知道该复核哪些用例。

这里补齐三件与业务无关的能力：
1. ``validate_assertions``：白名单校验（kind 必须是编译器认识的种类 + 必填字段 + 取值一致），
   错误信息结构化，便于批量预览；
2. ``preview_assertions``：**用真正的编译器**（``compile_cases``）预演这条用例能否转成可执行，
   返回 ``compile_status`` / ``compile_reason``，避免"写进去才发现不可编译"；
3. ``merge_assertions``：回写并打上 ``assertions_source``（``manual`` / ``doc_extracted``）与
   时间戳，让"文档变更后需要人工复核的断言"可被一条查询列出。
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Iterable

from app.services.web_case_compiler import FORM_CHECK_KINDS, compile_cases

#: 编译器认识的断言种类（单一事实来源：web_case_compiler.FORM_CHECK_KINDS）
ASSERTION_KINDS: frozenset[str] = frozenset(FORM_CHECK_KINDS)

#: 允许的预期取值（执行器只按这些语义判定）
ASSERTION_EXPECTS: frozenset[str] = frozenset({"accept", "reject", "visible", "hidden"})

#: 需要 ``data`` 的种类（无 data 就没有可判定的样本）
_KINDS_REQUIRING_DATA = frozenset({
    "boundary", "range", "precision", "max_length", "max_count", "enum", "default",
    "file_format", "file_size",
})

#: 断言来源取值
SOURCE_MANUAL = "manual"
SOURCE_DOC = "doc_extracted"

#: ``requirement_rule`` 顶层合法键。
#:
#: 取值依据（2026-10-17 实测本项目库内出现次数）：``field`` 1148 / ``type`` 842 /
#: ``source`` 676 / ``condition`` 659 / ``required`` 659 / ``rule`` 489 /
#: ``description`` 134 / ``evidence`` 78 / ``editable`` 30；其余为需求解析侧已定义、
#: 尚未落库的键。
#:
#: 白名单只用于抓**拼错键名**，不是 schema 校验：缺键合法（补丁语义），
#: 未知键不合法——否则 ``requried`` 这类笔误会被静默写进去，回写看着成功、实际没生效。
RULE_KEYS: frozenset[str] = frozenset({
    "field", "type", "source", "condition", "required", "rule",
    "description", "evidence", "editable",
    "options", "kind", "limit", "min", "max", "pattern", "default", "label",
})


class RulePatchError(ValueError):
    """规则体补丁不合法（未知键 / 非对象）。由接口层转成逐条 invalid，不中断整批。"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def merge_rule_patch(
    existing: Any, patch: dict[str, Any] | None
) -> dict[str, Any]:
    """把 ``patch`` **稀疏合并**进既有规则体（顶层补丁；拒绝未知键）。

    语义约定（2026-10-17 定稿，修复"整块替换导致 type/required/condition 全丢"）：

    - ``patch`` 为 ``None``：规则体原样不动（调用方只想改断言，不想碰规则）；
    - **缺键 = 保留原值**：调用方是 LLM / 前端表单，它不知道历史上有哪些键，
      要求"替换必须全量"等于逼它凭空编造 ``type``（库内仅 73% 用例有该键）；
    - **显式 ``null`` = 删除该键**：补丁语义的经典缺陷是"想清空却清不掉"，
      用 ``null`` 表达删除，既保留稀疏的好处又没有单向性；
    - **未知键 = 报错**：拼错的键名必须响，不能被当成新字段静默写入。

    .. warning:: **本函数只做顶层补丁，不是 RFC 7386 的完整实现。**
       嵌套对象**整体替换**，不做递归合并：``{"source": {"row": 2}}`` 会把整个
       ``source`` 换掉，而不是只改 ``source.row``。这是有意为之——规则体的值形态
       不统一（既有字符串也有对象），递归合并会在"字符串 vs 对象"之间产生不可预期
       的结果。需要改嵌套字段时，请提交完整的嵌套对象。

    **未知键的两条路径**（2026-10-17 补，回应"白名单与可修正性矛盾"）：

    - 未知键 + 非 ``null`` 值 → 报错（拼写错误不能静默写入）；
    - 未知键 + ``null`` 且**该键确实存在** → 允许，作为清理历史脏键的受控出口；
    - 未知键 + ``null`` 但**不存在** → 仍报错（否则 ``{"requried": null}``
      这种纯笔误会被当成"清理成功"）。

    历史脏键本身不参与校验，否则脏数据会永久无法修正。
    """
    base = dict(existing) if isinstance(existing, dict) else {}
    if patch is None:
        return base
    if not isinstance(patch, dict):
        raise RulePatchError("requirement_rule 必须是对象或 null")

    unknown_writes = sorted(
        key for key, value in patch.items() if key not in RULE_KEYS and value is not None
    )
    if unknown_writes:
        raise RulePatchError(
            "requirement_rule 含未知键："
            + "/".join(unknown_writes)
            + f"（允许：{'/'.join(sorted(RULE_KEYS))}）"
        )
    unknown_deletes = sorted(
        key for key, value in patch.items()
        if key not in RULE_KEYS and value is None and key not in base
    )
    if unknown_deletes:
        raise RulePatchError(
            "无法删除未知键："
            + "/".join(unknown_deletes)
            + "（规则体中不存在该键，请检查拼写；清理历史脏键请用值为 null 且键确实存在的补丁）"
        )

    for key, value in patch.items():
        if value is None:
            base.pop(key, None)
        else:
            base[key] = value
    return base


def validate_assertions(assertions: Any) -> list[str]:
    """校验断言列表，返回错误信息列表（空列表=通过）。"""
    errors: list[str] = []
    if not isinstance(assertions, list) or not assertions:
        return ["assertions 必须是非空数组"]
    for index, item in enumerate(assertions):
        prefix = f"[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} 断言必须是对象")
            continue
        kind = str(item.get("kind") or "").strip()
        if not kind:
            errors.append(f"{prefix} 缺少 kind")
        elif kind not in ASSERTION_KINDS:
            errors.append(
                f"{prefix} 不支持的 kind='{kind}'（支持：{'/'.join(sorted(ASSERTION_KINDS))}）"
            )
        expect = str(item.get("expect") or "").strip()
        if expect and expect not in ASSERTION_EXPECTS:
            errors.append(
                f"{prefix} expect='{expect}' 非法（支持：{'/'.join(sorted(ASSERTION_EXPECTS))}）"
            )
        if kind in _KINDS_REQUIRING_DATA and not str(item.get("data") or "").strip():
            errors.append(f"{prefix} kind='{kind}' 需要 data（可判定样本）")
        if not str(item.get("rule") or "").strip():
            # 断言必须能回链到需求原文，否则事后无法判断"文档改了要不要改断言"
            errors.append(f"{prefix} 缺少 rule（需求原文依据）")
        if kind != "visibility" and not str(item.get("field") or "").strip():
            errors.append(f"{prefix} 缺少 field（断言作用于哪个字段）")
    return errors


def preview_assertions(
    identifier: str,
    assertions: Any,
    *,
    name: str = "",
    rule_id: str = "",
    rule_field: str = "",
    custom_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """用真正的编译器预演"这条用例能不能自动执行"（不落库）。

    ``custom_fields``（2026-10-17 新增）：**合并后的完整字段**。传入时直接拿它去编译，
    与落库用的是同一份数据；不传时退回"用 ``rule_field`` 造一个最小规则体"的旧行为。

    为什么必须支持前者：旧实现（``:102-106``）在预览里自己拼
    ``{"field": rule_field or assertions[0].field}``，而保存走的是另一套合并逻辑，
    于是出现"预览说 compiled、落库后重编译零检查"——两套输入必然对不上。
    """
    errors = validate_assertions(assertions)
    preview: dict[str, Any] = {
        "identifier": identifier,
        "valid": not errors,
        "errors": errors,
    }
    if errors:
        preview["compile_status"] = ""
        preview["compile_reason"] = "断言校验未通过，未做编译预演"
        preview["assertion_count"] = 0
        preview["auto_executable"] = False
        return preview

    if custom_fields is not None:
        # 与保存同源：调用方传入的已经是 merge 之后的结果，这里只覆盖本次入参
        probe_fields = dict(custom_fields)
        probe_fields["assertions"] = [dict(item) for item in assertions]
        if rule_id:
            probe_fields["requirement_rule_id"] = rule_id
    else:
        probe_fields = {
            "assertions": [dict(item) for item in assertions],
            "requirement_rule_id": rule_id or "",
            "requirement_rule": {"field": rule_field or str(assertions[0].get("field") or "")},
        }
    probe = SimpleNamespace(identifier=identifier, name=name or identifier,
                            custom_fields=probe_fields)
    manifest = compile_cases([probe])
    entry = (manifest.get("cases") or [{}])[0]
    preview.update({
        "compile_status": entry.get("compile_status", ""),
        "compile_reason": entry.get("compile_reason", ""),
        "assertion_count": len(entry.get("assertions") or []),
        "auto_executable": entry.get("compile_status") == "compiled",
    })
    return preview


def merge_assertions(
    custom_fields: dict[str, Any] | None,
    assertions: Iterable[dict[str, Any]],
    *,
    rule_id: str | None = None,
    rule_field: str | None = None,
    requirement_rule: dict[str, Any] | None = None,
    source: str = SOURCE_MANUAL,
    updated_at: str | None = None,
    touch: bool = True,
) -> dict[str, Any]:
    """把断言与规则体合并进 ``custom_fields``（保留既有字段），并记录来源。

    两类对象的语义**刻意不同**：

    - ``requirement_rule``（对象）→ **稀疏补丁**，见 :func:`merge_rule_patch`。
      只给了 ``rule_field`` 时也只补 ``field`` 一个键，绝不整块替换——
      这正是 2026-10-17 之前的缺陷：``{"field": rule_field}`` 覆盖后
      ``type/required/condition/source`` 全部丢失，编译器从此只能靠一行 field 工作。
    - ``assertions``（数组）→ **整体替换**。数组没有"按键合并"的语义，
      而断言是这条用例的可判定样本**全集**；部分合并会留下过期样本
      （把 boundary 改成 max_length 后旧 boundary 还在，跑出假失败）。

    ``touch=False`` 供 dry_run 预览使用，不写时间戳。
    """
    fields = dict(custom_fields or {})
    assertion_list = [dict(item) for item in assertions]
    fields["assertions"] = assertion_list
    if rule_id is not None:
        fields["requirement_rule_id"] = rule_id

    existing_rule = fields.get("requirement_rule")
    old_field = str((existing_rule or {}).get("field") or "") if isinstance(existing_rule, dict) else ""
    patch: dict[str, Any] = dict(requirement_rule) if requirement_rule is not None else {}
    if rule_field is not None and "field" not in patch:
        patch["field"] = rule_field
    if patch:
        fields["requirement_rule"] = merge_rule_patch(existing_rule, patch)
    elif requirement_rule is not None:
        # 显式传空对象 {} ⇒ 按补丁语义是"什么都不改"，仍是原值
        fields["requirement_rule"] = merge_rule_patch(existing_rule, {})

    merged_field = str(((fields.get("requirement_rule") or {}).get("field")) or "")
    # 警告必须**按本次合并结果重算**：旧实现只在满足条件时写入、不满足时不动，
    # 于是上一次的告警会永久留在用例上（问题已改掉、提示还在），比没有警告更误导。
    fields.pop("assertions_warnings", None)
    if old_field and merged_field and old_field != merged_field:
        assertion_fields = {str(a.get("field") or "") for a in assertion_list if a.get("field")}
        if assertion_fields and merged_field not in assertion_fields:
            # 不阻断：R1 落地后断言自带 field 优先，规则体 field 只是兜底；
            # 硬 400 会让"只想改展示字段名"这种正常操作变成不可执行。
            fields["assertions_warnings"] = [
                f"规则体 field 由「{old_field}」改为「{merged_field}」，"
                f"但断言仍指向 {'/'.join(sorted(assertion_fields))}；"
                "请确认断言字段是否需要同步"
            ]

    fields["assertions_source"] = source
    if touch:
        fields["assertions_updated_at"] = updated_at or _utc_now()
    return fields


def assertions_needing_review(cases: Iterable[Any]) -> list[dict[str, Any]]:
    """列出**人工回写**的断言（文档变更后需要复核的对象）。"""
    out: list[dict[str, Any]] = []
    for case in cases:
        fields = getattr(case, "custom_fields", None) or {}
        if fields.get("assertions_source") != SOURCE_MANUAL:
            continue
        out.append({
            "identifier": getattr(case, "identifier", ""),
            "name": getattr(case, "name", ""),
            "rule_id": fields.get("requirement_rule_id") or "",
            "assertion_count": len(fields.get("assertions") or []),
            "updated_at": fields.get("assertions_updated_at") or "",
        })
    return out
