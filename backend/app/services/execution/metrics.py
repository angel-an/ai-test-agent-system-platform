"""执行治理只读指标（评审路线图第 4 步：最后指标）。

只读聚合（不写库），供运维/验收：
- executable_rate：有 effective 脚本的子功能 / Web 子功能总数
  （可执行覆盖率，脚本来源授权链 baseline）；
- heal_adoption_rate：自愈被**审批采用**（approve → passed）占自愈完成
  （approve+reject）的比例——rev55 审批流后，"修复了"≠"生效了"；
- human_oracle_rate：expected_result 无法编译（human_oracle）条数占
  expected_result 总条数比例（rev56 断言编译后，人工判定依赖度）；
- flaky_rate：**需要足够重复运行数据**（同脚本多次 run 结果冲突）才可算；
  数据不足时返回 None 与说明，不估算。
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.models.web_function import WebSubFunction
from app.models.web_script_review import WebScriptReview


async def compute_execution_governance_metrics(
    session_factory,
) -> dict:
    """计算执行治理指标（只读）。"""
    async with session_factory() as session:
        total_sf = (
            await session.execute(select(func.count(WebSubFunction.id)))
        ).scalar() or 0

        # executable：子功能存在 effective 脚本附件（web_script_registry 关联）
        # 最小统计：registry effective 行所属子功能数（去重）
        from app.models.attachment import Attachment
        from app.models.web_script_registry import WebScriptRegistry

        eff_sf_rows = (
            await session.execute(
                select(func.count(func.distinct(Attachment.entity_id)))
                .select_from(Attachment)
                .join(WebScriptRegistry, WebScriptRegistry.attachment_id == Attachment.id)
                .where(WebScriptRegistry.version_status == "effective")
            )
        ).scalar() or 0
        executable_rate = round(eff_sf_rows / total_sf, 4) if total_sf else 0.0

        # heal：rev55 审批流——adopted=passed（经 approve），completed=passed+rejected
        status_rows = (
            await session.execute(
                select(WebScriptReview.status, func.count(WebScriptReview.id))
                .where(WebScriptReview.status.in_(["passed", "rejected"]))
                .group_by(WebScriptReview.status)
            )
        ).all()
        counts = {s: int(c) for s, c in status_rows}
        adopted = counts.get("passed", 0)
        rejected = counts.get("rejected", 0)
        heal_total = adopted + rejected
        heal_adoption_rate = round(adopted / heal_total, 4) if heal_total else 0.0

        # human_oracle：重编译有 expected_results 的子功能，统计无法编译条数
        sf_with_er = (
            await session.execute(
                select(WebSubFunction.id, WebSubFunction.expected_results).where(
                    WebSubFunction.expected_results.is_not(None)
                )
            )
        ).all()
        from app.agents.tools.web.assertion_compiler import compile_expected_results

        total_er_items = 0
        total_human_oracle = 0
        for _, er in sf_with_er:
            if not er:
                continue
            _c = compile_expected_results(er)
            total_er_items += len(_c["assertions"]) + len(_c["human_oracle"])
            total_human_oracle += len(_c["human_oracle"])
        human_oracle_rate = (
            round(total_human_oracle / total_er_items, 4) if total_er_items else 0.0
        )

        # flaky（rev58）：基于历史 run——同一 WebTest ≥2 次 run 且状态冲突
        # （completed/failed 混存）→ flaky。min_runs=2 即可识别；样本越多越可靠。
        from app.models.web_test import WebTestRun

        wt_rows = (
            await session.execute(
                select(WebTestRun.web_test_id, WebTestRun.status)
            )
        ).all()
        by_wt: dict = {}
        for wt_id, st in wt_rows:
            by_wt.setdefault(str(wt_id), []).append(str(st))
        flaky_candidates = [sts for sts in by_wt.values() if len(sts) >= 2]
        flaky_count = sum(
            1 for sts in flaky_candidates if len(set(sts)) >= 2
        )
        flaky_rate = None
        flaky_note = (
            "需要足够重复运行数据（同脚本多次 run 结果冲突）才可计算。"
        )
        if flaky_candidates:
            flaky_rate = round(flaky_count / len(flaky_candidates), 4)
            flaky_note = (
                f"基于历史 run（WebTest 分组，≥2 次）：{flaky_count}/{len(flaky_candidates)}"
                f" 冲突；min_runs=2 即可识别，样本越多越可靠。"
            )

    return {
        "executable_rate": executable_rate,
        "executable": {"sub_functions_with_effective": eff_sf_rows,
                       "total_sub_functions": total_sf},
        "heal_adoption_rate": heal_adoption_rate,
        "heal": {"adopted": adopted, "rejected": rejected},
        "human_oracle_rate": human_oracle_rate,
        "human_oracle": {"total_expected_items": total_er_items,
                         "human_oracle_items": total_human_oracle},
        "flaky_rate": flaky_rate,
        "flaky": {"note": flaky_note,
                  "sampled_groups": len(flaky_candidates),
                  "flaky_groups": flaky_count},
    }


async def sample_script_flakiness(
    session_factory,
    sub_function_id,
    runs: int = 3,
    project_identifier: str = "",
) -> dict:
    """flaky 重复运行采样（rev58）：对指定子功能的当前 effective 脚本连续执行
    runs 次，比较结果一致性。

    注意：真实执行消耗资源（浏览器/脚本运行时间）；runs 建议 3-5。
    """
    from uuid import UUID

    from sqlalchemy import select as _select

    from app.models.web_test import WebTest

    runs = max(2, min(runs, 5))
    async with session_factory() as session:
        wt = (
            await session.execute(
                _select(WebTest).where(WebTest.sub_function_id == UUID(str(sub_function_id)))
                .order_by(WebTest.created_at.desc()).limit(1)
            )
        ).scalars().first()
        if wt is None or not wt.script_path:
            return {"error": "子功能无 WebTest/脚本，无法采样"}
        wt_id = str(wt.id)
        # 若未给项目标识，从 WebTest.project_id 反查
        if not project_identifier:
            from app.models.project import Project

            proj = (
                await session.execute(
                    _select(Project).where(Project.id == wt.project_id)
                )
            ).scalars().first()
            project_identifier = proj.identifier if proj else ""

    from app.services.web_test_service import WebTestService

    statuses = []
    errors = []
    async with session_factory() as session:
        svc = WebTestService(session)
        for i in range(runs):
            try:
                r = await svc.run_web_test(project_identifier, wt_id, {"timeout": 300})
                statuses.append(str(r.get("status", "?")))
                if r.get("error_message"):
                    errors.append(str(r["error_message"])[:120])
            except Exception as e:
                statuses.append("error")
                errors.append(str(e)[:120])
    unique = set(statuses)
    flaky = len(statuses) >= 2 and len(unique) >= 2
    # flaky_rate = 非众数状态占比（全一致 → 0；2 状态各半 → 0.5）
    _max_cnt = max((statuses.count(s) for s in unique), default=0)
    flaky_rate = round((len(statuses) - _max_cnt) / len(statuses), 4) if statuses else 0.0
    return {
        "sub_function_id": str(sub_function_id),
        "runs": runs,
        "statuses": statuses,
        "flaky": flaky,
        "flaky_rate": flaky_rate,
        "errors": errors[:3],
    }
