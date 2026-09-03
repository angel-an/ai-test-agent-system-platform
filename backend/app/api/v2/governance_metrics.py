"""执行治理只读指标 API（rev57，评审路线图第 4 步）。

GET /api/v2/governance-metrics —— 只读聚合：
executable_rate / heal_adoption_rate / human_oracle_rate；flaky_rate 数据不足
时返回 None 并说明。仅超级管理员可读。
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_current_superuser
from app.config.database import async_session_factory
from app.models.user import User

router = APIRouter(prefix="/governance-metrics", tags=["执行治理指标"])
logger = logging.getLogger(__name__)


class SampleFlakinessRequest(BaseModel):
    sub_function_id: str
    runs: int = 3
    project_identifier: str = ""


@router.get("/")
async def get_governance_metrics(
    current_user: User = Depends(get_current_superuser),
):
    """执行治理只读指标（超管可见）。"""
    from app.services.execution.metrics import compute_execution_governance_metrics

    return await compute_execution_governance_metrics(async_session_factory)


@router.get("/ui")
async def governance_metrics_panel(
    current_user: User = Depends(get_current_superuser),
):
    """执行治理只读面板（rev58）：服务端渲染 HTML，展示指标与明细（超管可见）。"""
    from fastapi.responses import HTMLResponse

    from app.services.execution.metrics import compute_execution_governance_metrics

    m = await compute_execution_governance_metrics(async_session_factory)

    def _row(label, value, hint):
        return (f"<tr><td class='lbl'>{label}</td><td class='val'>{value}</td>"
                f"<td class='hint'>{hint}</td></tr>")

    rows = "".join([
        _row("executable_rate", f"{m['executable_rate'] * 100:.1f}%",
             f"{m['executable']['sub_functions_with_effective']}/{m['executable']['total_sub_functions']} "
             "子功能有 effective 脚本"),
        _row("heal_adoption_rate", f"{m['heal_adoption_rate'] * 100:.1f}%",
             f"{m['heal']['adopted']} 采纳 / {m['heal']['rejected']} 拒绝（rev55 审批流）"),
        _row("human_oracle_rate", f"{m['human_oracle_rate'] * 100:.1f}%",
             f"{m['human_oracle']['human_oracle_items']}/{m['human_oracle']['total_expected_items']} "
             "条 expected_result 需人工判定"),
        _row("flaky_rate",
             f"{m['flaky_rate'] * 100:.1f}%" if m["flaky_rate"] is not None else "—",
             m["flaky"]["note"]),
    ])
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>执行治理指标</title>
<style>
body {{ font-family: -apple-system, sans-serif; background: #f5f7fa; padding: 24px; }}
.container {{ max-width: 900px; margin: 0 auto; background: #fff; border-radius: 12px;
              padding: 24px 32px; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }}
h1 {{ font-size: 20px; color: #1a1a2e; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
th, td {{ border: 1px solid #e8e8e8; padding: 10px 14px; text-align: left; font-size: 14px; }}
th {{ background: #fafafa; }}
.lbl {{ width: 26%; font-weight: 600; }}
.val {{ width: 16%; font-size: 18px; color: #1677ff; }}
.hint {{ color: #888; font-size: 13px; }}
.note {{ color: #888; font-size: 13px; margin-top: 16px; }}
</style></head><body><div class="container">
<h1>📊 执行治理指标（只读）</h1>
<p class="note">数据源：registry（effective 脚本）/ web_script_reviews（审批流）/
web_sub_functions.expected_results（断言编译）/ web_test_runs（flaky 历史）。
更新于 {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
<table><tr><th>指标</th><th>当前值</th><th>口径</th></tr>{rows}</table>
<p class="note">采样：POST /api/v2/governance-metrics/sample-flakiness
{{sub_function_id, runs}}（对指定脚本连续重跑比较一致性，真实执行消耗资源）。</p>
</div></body></html>"""
    return HTMLResponse(content=html)


@router.post("/sample-flakiness")
async def sample_flakiness(
    body: SampleFlakinessRequest,
    current_user: User = Depends(get_current_superuser),
):
    """flaky 重复运行采样（rev58）：对指定子功能当前脚本连续执行 runs 次
    （2-5，真实执行消耗资源），比较结果一致性。

    仅超级管理员可调用。
    """
    from uuid import UUID

    from app.services.execution.metrics import sample_script_flakiness

    result = await sample_script_flakiness(
        async_session_factory,
        UUID(body.sub_function_id),
        runs=body.runs,
        project_identifier=body.project_identifier,
    )
    logger.info(
        "[audit] actor=%s(%s) action=governance_metrics.sample_flakiness "
        "sub_function=%s runs=%s flaky=%s",
        current_user.username, current_user.id, body.sub_function_id,
        body.runs, result.get("flaky"),
    )
    return result
