# -*- coding: utf-8 -*-
"""历史批次「用例 → 执行资产」绑定回填（rev95 之前生成的批次补写 web_coverage）。

背景（2026-09-29 链路打通复核）
--------------------------------
rev95 起 ``create_minimal_loop`` 才逐条回写绑定（``build_case_asset_bindings``），
此前生成的批次 ``custom_fields`` 里没有 ``web_coverage``——用例侧看不到自己属于
哪个资产，"这条用例是否被自动化覆盖"无法回答（PR-9 实测 85 条只有 7 条可追溯；
CS 重建后现状：35 条带断言用例中约 31 条未绑定）。

血缘恢复依据（不新建任何资产）
------------------------------
- ``TestRunScriptJob``（script_type=WEB_TEST, script_id=WebTest.id）× 同 run 的
  ``TestRunTestCase`` 行（minimal-loop 建运行时全量用例入 run）
  → 资产 ↔ 批次用例集合（一个资产多个 run 取并集）；项目范围由
  ``WebTest.project_id`` 限定（作业行本身不跨项目污染）；
- 用例当前可执行性由 ``custom_fields.assertions`` 决定 → ``compile_cases`` 重算
  manifest（compile_status / assertion_count），与四指标同口径。

口径（与 rev95 一致，**不得**把"绑定了资产"读成"已被自动覆盖"）
--------------------------------------------------------------
- 绑定 = 血缘可追溯：批次内**每条**用例都写（含无断言的叙述型），
  ``web_coverage.auto_executable=False`` / ``assertion_count=0`` 如实表达；
- 主用例判定不做业务猜测（确定性）：已锚定本资产的用例 → ``WebTest.test_case_id``
  → 批次首条（created_at → identifier 排序）。

本工具默认只读（dry-run），``--apply`` 才写库；绑定函数自带幂等
（同资产同覆盖跳过），重复执行不会重复改写。
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import async_session_factory
from app.models.project import Project
from app.models.test_case import TestCase
from app.models.test_run import TestRunScriptJob, TestRunTestCase
from app.models.web_test import WebTest
from app.schemas.enums import ScriptType
from app.services.web_case_compiler import compile_cases
from app.services.web_case_link_service import build_case_asset_bindings


def compute_group_plan(
    *,
    web_test_id: Any,
    web_test_identifier: str,
    batch_cases: list[Any],
    primary_case: Any,
    navigation_path: Any,
    path_template_id: Any,
    path_match_status: str,
    base_url_source: Any,
    login_profile: Any,
    sub_function_id: Any,
    bound_at: str,
) -> list[tuple[Any, dict[str, Any]]]:
    """纯函数：重算 manifest 并计算绑定更新（幂等，dry-run 与 apply 共用）。

    返回 [(case, new_fields), ...]，只含**需要变更**的用例
    （``build_case_asset_bindings`` 内部对同资产同覆盖跳过）。
    """
    manifest = compile_cases(batch_cases)
    return build_case_asset_bindings(
        web_test_id=web_test_id,
        web_test_identifier=web_test_identifier,
        manifest=manifest,
        batch_cases=batch_cases,
        primary_case_identifier=getattr(primary_case, "identifier", None),
        navigation_path=navigation_path,
        path_template_id=path_template_id,
        path_match_status=path_match_status,
        base_url_source=base_url_source,
        login_profile=login_profile,
        sub_function_id=sub_function_id,
        bound_at=bound_at,
    )


def _resolve_binding_params(
    *,
    web_test: Any,
    primary_case: Any,
) -> dict[str, Any]:
    """绑定参数来源：优先主用例既有 custom_fields（pre-rev95 主用例已带路径字段），
    兜底 WebTest.generation_params（web_path_template_id / sub_function_id 等）。"""
    primary_fields = dict(getattr(primary_case, "custom_fields", None) or {})
    gp = dict(getattr(web_test, "generation_params", None) or {})
    match_status = primary_fields.get("web_path_match_status")
    if match_status not in {"matched", "pending_confirmation"}:
        gp_status = gp.get("web_path_match_status")
        match_status = gp_status if gp_status in {"matched", "pending_confirmation"} else "pending_confirmation"
    return {
        "navigation_path": primary_fields.get("web_validation_path"),
        "path_template_id": primary_fields.get("web_path_template_id") or gp.get("web_path_template_id"),
        "path_match_status": match_status,
        "base_url_source": primary_fields.get("web_path_base_url_source"),
        "login_profile": primary_fields.get("login_profile"),
        "sub_function_id": primary_fields.get("sub_function_id") or gp.get("sub_function_id"),
    }


def _select_primary(batch_cases: list[Any], web_test: Any) -> Any:
    """主用例判定（确定性，不猜业务）：

    1. 已带 ``web_primary_case=True`` 标记的用例（保持历史决策稳定——回填后批次内
       每条用例都带 ``web_test_id``，只看锚定会退化成依赖库返回顺序，实测导致
       role 在两次执行间翻转、幂等永不收敛）；
    2. ``WebTest.test_case_id`` 锚点；
    3. 已锚定本资产的用例（pre-rev95 只有主用例带 ``web_test_id``），
       按 created_at → identifier 排序取首条；
    4. 批次首条（同上排序）。
    """
    for case in batch_cases:
        fields = getattr(case, "custom_fields", None) or {}
        if fields.get("web_primary_case") is True:
            return case
    anchor_case_id = getattr(web_test, "test_case_id", None)
    if anchor_case_id is not None:
        for case in batch_cases:
            if getattr(case, "id", None) == anchor_case_id:
                return case
    anchor = str(web_test.id)
    anchored = [
        c for c in batch_cases
        if str((getattr(c, "custom_fields", None) or {}).get("web_test_id") or "") == anchor
    ]
    pool = anchored or batch_cases
    ordered = sorted(
        pool,
        key=lambda c: (str(getattr(c, "created_at", "") or ""), str(getattr(c, "identifier", "") or "")),
    )
    return ordered[0] if ordered else None


def _partition_include(
    cases: list[Any],
    *,
    web_test_id: Any,
    valid_asset_ids: set[str],
    winner_id_by_case: dict[Any, Any],
) -> tuple[list[Any], list[Any], list[Any]]:
    """把资产批次用例分成（纳入本次绑定, 归属其他资产跳过, 孤儿绑定修复）。

    规则（不做业务猜测，确定性）：
    - 已绑定**本资产** → 纳入（幂等刷新路径）；
    - 已绑定**其他仍存在**的资产 → 跳过（绑定不动，避免多资产互相抢写翻转）。
      "仍存在" = WebTest 行在库（retention 归档不删行，归档资产仍视为有效归属）；
    - 已绑定但目标资产**已不存在**（孤儿绑定）→ 视同未绑定，参与归属判定，
      单独计数为 orphan_repaired 供审计；
    - 未绑定且被多个资产声明（争议）→ 只归属**最新创建**的资产
      （``winner_id_by_case``，平手取 identifier 较大者），其余声明方跳过。
    """
    include: list[Any] = []
    elsewhere: list[Any] = []
    orphan: list[Any] = []
    for case in cases:
        fields = getattr(case, "custom_fields", None) or {}
        wid = str(fields.get("web_test_id") or "")
        if wid == str(web_test_id):
            include.append(case)
            continue
        if wid and wid in valid_asset_ids:
            elsewhere.append(case)  # 别人的绑定，不动
            continue
        if wid:
            orphan.append(case)  # 绑定的资产行已不存在
        if winner_id_by_case.get(getattr(case, "id", None)) == web_test_id:
            include.append(case)
        else:
            elsewhere.append(case)
    return include, elsewhere, orphan


async def _iter_group_plans(
    session: AsyncSession,
    project: Project,
    bound_at: str,
) -> AsyncIterator[dict[str, Any]]:
    """逐资产产出计划组（含审计计数）。

    返回 dict：web_test / cases（纳入）/ plan（[(case, fields)]) / auto /
    elsewhere（归属其他资产跳过）/ orphan（孤儿绑定修复）/
    created（plan 中首次绑定本资产的）/ updated（plan 中刷新既有绑定的）。
    """
    jobs = list((await session.execute(
        select(TestRunScriptJob).where(TestRunScriptJob.script_type == ScriptType.WEB_TEST)
    )).scalars().all())
    asset_cases: dict[Any, set[Any]] = defaultdict(set)
    for job in jobs:
        rows = (await session.execute(
            select(TestRunTestCase.test_case_id).where(
                TestRunTestCase.test_run_id == job.test_run_id
            )
        )).scalars().all()
        asset_cases[job.script_id].update(rows)
    if not asset_cases:
        return
    assets = [
        a for a in (await session.execute(
            select(WebTest).where(WebTest.id.in_(list(asset_cases.keys())))
        )).scalars().all()
        if a.project_id == project.id
    ]
    valid_asset_ids = {str(a.id) for a in assets}
    # 争议归属：未绑定用例被多个资产声明时 → 归属最新创建（created_at，平手 identifier）的资产
    case_claimants: dict[Any, list[Any]] = defaultdict(list)
    for asset in assets:
        for cid in asset_cases.get(asset.id) or set():
            case_claimants[cid].append(asset)
    winner_id_by_case = {
        cid: max(claimants, key=lambda a: (str(a.created_at or ""), str(a.identifier))).id
        for cid, claimants in case_claimants.items()
    }
    for web_test in sorted(assets, key=lambda a: str(a.identifier)):
        case_ids = asset_cases.get(web_test.id) or set()
        cases: list[Any] = []
        if case_ids:
            cases = list((await session.execute(
                select(TestCase).where(TestCase.id.in_(list(case_ids)))
            )).scalars().all())
        # WebTest 锚点用例不在 run 组内时也纳入批次（与 create_minimal_loop 一致）
        anchor_id = getattr(web_test, "test_case_id", None)
        if anchor_id is not None and anchor_id not in case_ids:
            anchor_case = await session.get(TestCase, anchor_id)
            if anchor_case is not None:
                cases.append(anchor_case)
        # deprecated=true 的废弃用例不参与回填（2026-09-29 复核：已废弃 ≠ 可自动绑定）
        from app.services.deprecation import filter_active, is_deprecated
        dropped = [c for c in cases if is_deprecated(c)]
        if dropped:
            print(
                f"    {web_test.identifier}: 跳过已废弃用例 "
                f"{[getattr(c, 'identifier', '?') for c in dropped]}"
            )
        cases = filter_active(cases)
        if not cases:
            continue
        include, elsewhere, orphan = _partition_include(
            cases,
            web_test_id=web_test.id,
            valid_asset_ids=valid_asset_ids,
            winner_id_by_case=winner_id_by_case,
        )
        if not include:
            yield {
                "web_test": web_test, "cases": [], "plan": [], "auto": 0,
                "elsewhere": len(elsewhere), "orphan": len(orphan),
                "created": 0, "updated": 0,
            }
            continue
        primary = _select_primary(include, web_test)
        params = _resolve_binding_params(web_test=web_test, primary_case=primary)
        plan = compute_group_plan(
            web_test_id=web_test.id,
            web_test_identifier=web_test.identifier,
            batch_cases=include,
            primary_case=primary,
            bound_at=bound_at,
            **params,
        )
        manifest = compile_cases(include)
        auto = sum(
            1 for c in manifest.get("cases") or []
            if c.get("compile_status") == "compiled" and c.get("assertions")
        )
        # 审计计数：plan 内拆分"首次绑定本资产"与"刷新既有绑定"
        created = sum(
            1 for case, _ in plan
            if str((getattr(case, "custom_fields", None) or {}).get("web_test_id") or "")
            != str(web_test.id)
        )
        yield {
            "web_test": web_test, "cases": include, "plan": plan, "auto": auto,
            "elsewhere": len(elsewhere), "orphan": len(orphan),
            "created": created, "updated": len(plan) - created,
        }


async def scan(project_identifier: str | None, apply: bool) -> int:
    bound_at = datetime.now().isoformat(timespec="seconds")
    async with async_session_factory() as session:
        if project_identifier:
            project = (await session.execute(
                select(Project).where(Project.identifier == project_identifier)
            )).scalar_one_or_none()
            if not project:
                print(f"[ERROR] 项目不存在: {project_identifier}")
                return 1
            projects = [project]
        else:
            projects = list((await session.execute(select(Project))).scalars().all())

        groups: list[dict[str, Any]] = []
        for project in projects:
            found = False
            async for group in _iter_group_plans(session, project, bound_at):
                found = True
                groups.append(group)
                web_test = group["web_test"]
                cases, plan = group["cases"], group["plan"]
                satisfied = len(cases) - len(plan)
                action = "将绑定" if plan else "无变更"
                print(
                    f"[{project.identifier}] {web_test.identifier}: 纳入 {len(cases)} 条 | "
                    f"可自动执行 {group['auto']} 条 | {action} {len(plan)} 条"
                    f"（首次 {group['created']} / 刷新 {group['updated']}）| "
                    f"已满足 {satisfied} 条 | 归属其他资产跳过 {group['elsewhere']} 条 | "
                    f"孤儿绑定修复 {group['orphan']} 条"
                    f"（主用例 {getattr(_select_primary(cases, web_test), 'identifier', '?') if cases else '-'}）"
                )
                for case, fields in plan[:10]:
                    cov = fields.get("web_coverage") or {}
                    print(
                        f"    {getattr(case, 'identifier', '?')}: role={cov.get('role')} "
                        f"compile_status={cov.get('compile_status')} "
                        f"assertion_count={cov.get('assertion_count')} "
                        f"auto_executable={cov.get('auto_executable')}"
                    )
                if len(plan) > 10:
                    print(f"    … 另有 {len(plan) - 10} 条")
            if not found:
                print(f"[{project.identifier}] 无 WEB_TEST 资产批次，跳过")

        # 审计计数（单位 = 绑定关系「用例×资产」，一条用例可被多个资产声明、
        # 但同一时刻只归属一个资产，因此去重后的用例数 ≤ 绑定关系数）
        planned = sum(len(g["plan"]) for g in groups)
        created = sum(g["created"] for g in groups)
        updated = sum(g["updated"] for g in groups)
        auto = sum(g["auto"] for g in groups)
        distinct_cases = {
            getattr(case, "id", None)
            for g in groups for case, _ in g["plan"]
        }
        print(
            f"\n汇总（单位=绑定关系[用例×资产]）：计划写入 {planned} 条"
            f"（首次绑定 {created} / 刷新 {updated}），"
            f"涉及资产 {len(groups)} 个、去重用例 {len(distinct_cases)} 条，"
            f"其中可自动执行口径覆盖 {auto} 条。"
        )
        if not apply:
            print("（dry-run：未写入。确认后加 --apply 执行。）")
            return 0

    # apply：重新打开会话写库（避免跨会话持有过期对象；绑定幂等，重算安全）
    print("\n开始写入 …")
    written = 0
    async with async_session_factory() as session2:
        for project in projects:
            async for group in _iter_group_plans(session2, project, bound_at):
                for case, fields in group["plan"]:
                    case.custom_fields = fields
                    written += 1
        await session2.commit()
    print(f"已提交：绑定回写 {written} 条（单位=绑定关系[用例×资产]）。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="历史批次用例→资产绑定回填（默认只读 dry-run）")
    parser.add_argument("--project", default=None, help="限定项目标识（如 PR-9）；默认全项目")
    parser.add_argument("--apply", action="store_true", help="写库（默认不执行）")
    args = parser.parse_args(argv)
    return asyncio.run(scan(args.project, args.apply))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
