"""APScheduler 定时任务管理"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, EVENT_JOB_MISSED

from app.config.database import async_session_factory
from app.models.scheduled_run import ScheduledRun
from app.repositories.scheduled_run_repo import (
    ScheduledRunRepository,
    ScheduledRunExecutionRepository,
)
from app.repositories.api_test_repo import APITestRunRepository

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def _job_listener(event):
    """监听 job 事件，记录日志便于排查"""
    if event.code == EVENT_JOB_MISSED:
        logger.warning(f"[scheduler] Job MISSED: {event.job_id} (scheduled_run: {event.job_id})")
        print(f"[scheduler] Job MISSED: {event.job_id}")
    elif event.code == EVENT_JOB_ERROR:
        logger.error(f"[scheduler] Job ERROR: {event.job_id}, exception: {event.exception}")
        print(f"[scheduler] Job ERROR: {event.job_id}, {event.exception}")
    elif event.code == EVENT_JOB_EXECUTED:
        logger.info(f"[scheduler] Job EXECUTED: {event.job_id}")
        print(f"[scheduler] Job EXECUTED: {event.job_id}")


scheduler.add_listener(_job_listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED)


async def _run_script_repair_cycle():
    """定时触发 Web 脚本评审修复循环（P0-2 生产接入，真实 LLM 修复 + proposed 验证）。

    run_pending_repairs 内部：扫描 pending → 逐个 run_repair_cycle
    （claim → LLM 修复 → save proposed → verify → publish / bump_retry）。
    """
    from app.agents.tools.web.script_review import run_pending_repairs

    try:
        results = await run_pending_repairs(async_session_factory)
        print(f"[scheduler] 脚本修复循环完成: {len(results)} 个任务")
        for r in results:
            print(
                f"[scheduler]   修复 {r['sub_function_id']}: {r['status']} "
                f"- {(r.get('detail') or '')[:80]}"
            )
    except Exception as e:  # 定时任务绝不因单次异常中断调度器
        logger.error(f"[scheduler] 脚本修复循环异常: {e}")
        print(f"[scheduler] 脚本修复循环异常: {e}")


def register_script_repair_job():
    """注册 Web 脚本评审修复定时任务。

    启用方式：WEB_SCRIPT_REPAIR_INTERVAL_MINUTES=5（分钟，>0 生效；默认 0 关闭，
    避免未经配置即消耗 LLM API 额度）。max_instances=1 + coalesce 防止并发重叠。
    """
    interval = int(os.environ.get("WEB_SCRIPT_REPAIR_INTERVAL_MINUTES", "0") or 0)
    if interval <= 0:
        print("[scheduler] 脚本修复定时任务未启用（WEB_SCRIPT_REPAIR_INTERVAL_MINUTES=0）")
        return
    scheduler.add_job(
        _run_script_repair_cycle,
        "interval",
        minutes=interval,
        id="web_script_repair_cycle",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    print(f"[scheduler] 注册脚本修复定时任务: 每 {interval} 分钟")


async def _execute_scheduled_run(scheduled_run_id: str):
    """执行一次定时运行（scheduler job 入口）"""
    print(f"[scheduler] 触发定时任务: {scheduled_run_id} at {datetime.now(timezone.utc).isoformat()}")
    logger.info(f"[scheduler] 触发定时任务: {scheduled_run_id}")
    async with async_session_factory() as session:
        try:
            await session.begin()
            repo = ScheduledRunRepository(session)
            exec_repo = ScheduledRunExecutionRepository(session)

            scheduled_run = await repo.get_by_id(UUID(scheduled_run_id))
            if not scheduled_run or not scheduled_run.is_active:
                print(f"[scheduler] ⏭️ 跳过任务 {scheduled_run_id}: {'不存在' if not scheduled_run else '已禁用'}")
                return

            # 创建执行记录
            identifier = await exec_repo.get_next_identifier(scheduled_run.id)
            execution = await exec_repo.create(
                scheduled_run_id=scheduled_run.id,
                project_id=scheduled_run.project_id,
                identifier=identifier,
                status="running",
                api_test_run_ids=[],
            )
            await session.commit()

            # 对每个 api_endpoint 查找测试脚本附件并执行
            from sqlalchemy import select
            from app.models.attachment import Attachment, AttachmentEntityType
            from app.models.api_endpoint import APIEndpoint
            from app.models.api_test import APITest
            from app.repositories.api_test_repo import APITestRepository
            from app.services.api_endpoint_script_executor import execute_endpoint_script

            run_ids = []
            for endpoint_id_str in scheduled_run.api_endpoint_ids:
                try:
                    endpoint = await session.get(APIEndpoint, UUID(endpoint_id_str))
                    if not endpoint:
                        print(f"[scheduler] endpoint {endpoint_id_str} 不存在，跳过")
                        continue

                    # 查找该端点下的测试脚本附件
                    result = await session.execute(
                        select(Attachment).where(
                            Attachment.entity_id == UUID(endpoint_id_str),
                            Attachment.entity_type == AttachmentEntityType.API_TEST_SCRIPT,
                        ).order_by(Attachment.created_at.desc()).limit(1)
                    )
                    attachment = result.scalar_one_or_none()
                    if not attachment:
                        print(f"[scheduler] endpoint {endpoint_id_str} 无测试脚本附件，跳过")
                        continue

                    # 解析端点关联的 api_test；FK 必须落在真实存在的 api_tests.id 上。
                    # 如果没有可用的 api_test，则基于该 attachment 兜底创建一个占位 api_test。
                    api_test_id: Optional[UUID] = None
                    for tid in (endpoint.api_test_ids or []):
                        candidate = await session.get(APITest, UUID(tid))
                        if candidate:
                            api_test_id = candidate.id
                            break

                    if not api_test_id:
                        api_test_repo = APITestRepository(session)
                        identifier = await api_test_repo.get_next_identifier(scheduled_run.project_id)
                        placeholder = await api_test_repo.create(
                            project_id=scheduled_run.project_id,
                            folder_id=endpoint.folder_id,
                            identifier=identifier,
                            name=f"{endpoint.display_name} 测试脚本",
                            description="由定时运行自动创建的占位 API 测试",
                            schema_type="openapi",
                            script_path=attachment.object_name,
                            script_format="playwright",
                            script_language="typescript",
                            test_config={},
                            generated_by_agent="scheduler",
                            total_endpoints=1,
                            total_scenarios=0,
                        )
                        api_test_id = placeholder.id

                        # 回填到端点的 api_test_ids，下次直接命中
                        existing_ids = list(endpoint.api_test_ids or [])
                        existing_ids.append(str(api_test_id))
                        endpoint.api_test_ids = existing_ids
                        session.add(endpoint)
                        print(
                            f"[scheduler] endpoint {endpoint_id_str} 自动创建占位 api_test "
                            f"{api_test_id} ({identifier})"
                        )

                    run_id = await execute_endpoint_script(
                        session=session,
                        attachment=attachment,
                        project_id=scheduled_run.project_id,
                        api_test_id=api_test_id,
                        execution_config=scheduled_run.execution_config or {},
                    )
                    run_ids.append(run_id)
                    await session.commit()
                except Exception as e:
                    print(f"[scheduler] endpoint {endpoint_id_str} 执行失败: {e}")
                    await session.rollback()

            # 等待所有 run 完成（轮询 api_test_runs.status）
            await _wait_for_runs(run_ids)

            # 汇总统计
            total, passed, failed = 0, 0, 0
            async with async_session_factory() as stat_session:
                stat_run_repo = APITestRunRepository(stat_session)
                for run_id in run_ids:
                    r = await stat_run_repo.get_by_id(UUID(run_id))
                    if r:
                        total += r.total_tests
                        passed += r.passed_tests
                        failed += r.failed_tests

            # 生成 HTML 报告
            report_path = await _generate_html_report(
                scheduled_run=scheduled_run,
                execution_id=str(execution.id),
                execution_identifier=execution.identifier,
                run_ids=run_ids,
                total=total,
                passed=passed,
                failed=failed,
            )

            # 更新执行记录
            async with async_session_factory() as update_session:
                update_exec_repo = ScheduledRunExecutionRepository(update_session)
                update_repo = ScheduledRunRepository(update_session)
                exec_obj = await update_exec_repo.get_by_id(execution.id)
                await update_exec_repo.update(
                    exec_obj,
                    status="completed",
                    api_test_run_ids=run_ids,
                    total_tests=total,
                    passed_tests=passed,
                    failed_tests=failed,
                    report_path=report_path,
                )
                sr = await update_repo.get_by_id(scheduled_run.id)
                await update_repo.update(
                    sr,
                    last_executed_at=datetime.now(timezone.utc).isoformat(),
                    last_execution_id=execution.id,
                )
                await update_session.commit()

        except Exception as e:
            print(f"[scheduler] 定时运行 {scheduled_run_id} 失败: {e}")
            async with async_session_factory() as err_session:
                err_exec_repo = ScheduledRunExecutionRepository(err_session)
                try:
                    exec_obj = await err_exec_repo.get_by_id(execution.id)
                    await err_exec_repo.update(exec_obj, status="failed", error_message=str(e))
                    await err_session.commit()
                except Exception:
                    pass


async def _wait_for_runs(run_ids: list, timeout: int = 600, interval: int = 5):
    """轮询等待所有 api_test_run 完成"""
    deadline = asyncio.get_event_loop().time() + timeout
    pending = set(run_ids)
    while pending and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(interval)
        async with async_session_factory() as session:
            repo = APITestRunRepository(session)
            done = set()
            for run_id in list(pending):
                r = await repo.get_by_id(UUID(run_id))
                if r and r.status in ("completed", "failed", "cancelled"):
                    done.add(run_id)
            pending -= done


async def _generate_html_report(
    scheduled_run: ScheduledRun,
    execution_id: str,
    execution_identifier: str,
    run_ids: list,
    total: int,
    passed: int,
    failed: int,
) -> Optional[str]:
    """生成自包含 HTML 报告（含图表）并上传 MinIO"""
    try:
        import html as html_lib
        import json as _json
        from app.config.minio_client import MinIOClient
        from app.models.api_test import APITestResult
        from sqlalchemy import select

        pass_rate = round(passed / total * 100, 1) if total > 0 else 0
        cfg = scheduled_run.execution_config or {}
        environment = cfg.get("environment") or cfg.get("env")
        base_url = cfg.get("base_url") or cfg.get("baseURL") or cfg.get("baseUrl")

        # 拉取明细结果
        results: list[APITestResult] = []
        if run_ids:
            async with async_session_factory() as detail_session:
                rs = await detail_session.execute(
                    select(APITestResult)
                    .where(APITestResult.test_run_id.in_([UUID(r) for r in run_ids]))
                    .order_by(APITestResult.created_at)
                )
                results = list(rs.scalars().all())

        # 场景覆盖：按 scenario_name 聚合，并构造短标签 + 完整 tooltip
        scenario_counts: dict[str, int] = {}
        scenario_status: dict[str, dict[str, int]] = {}
        for r in results:
            key = r.scenario_name or r.endpoint or "-"
            scenario_counts[key] = scenario_counts.get(key, 0) + 1
            stv = r.status.value if hasattr(r.status, "value") else str(r.status)
            bucket = scenario_status.setdefault(key, {"passed": 0, "failed": 0, "other": 0})
            if stv == "passed":
                bucket["passed"] += 1
            elif stv == "failed":
                bucket["failed"] += 1
            else:
                bucket["other"] += 1

        def _short_label(name: str, idx: int) -> str:
            """生成简短标签：如果开头有形如 'TC-XXX-001' 的编号则用编号，否则取前 14 字符。"""
            import re as _re
            m = _re.match(r"^([A-Za-z]+-[一-龥A-Za-z0-9]+-\d+)", name or "")
            if m:
                return m.group(1)
            base = (name or f"#{idx + 1}").strip()
            return (base[:14] + "…") if len(base) > 14 else base

        full_labels = list(scenario_counts.keys())
        scenario_labels = [_short_label(n, i) for i, n in enumerate(full_labels)]
        scenario_values = list(scenario_counts.values())
        scenario_pass = [scenario_status[n]["passed"] for n in full_labels]
        scenario_fail = [scenario_status[n]["failed"] for n in full_labels]

        # 详细行
        rows_html_parts = []
        for idx, r in enumerate(results, 1):
            status_value = r.status.value if hasattr(r.status, "value") else str(r.status)
            badge_cls = "pass" if status_value == "passed" else ("fail" if status_value == "failed" else "skip")
            badge_text = {"passed": "通过", "failed": "失败", "skipped": "跳过", "blocked": "阻塞"}.get(status_value, status_value)
            resp = r.response_summary or {}
            status_code = resp.get("status_code")
            status_code_html = html_lib.escape(str(status_code)) if status_code else "<span style='color:#94a3b8'>-</span>"
            req = r.request_summary or {}
            body = req.get("body_summary") or req.get("body") or req.get("query")
            if body:
                try:
                    params_text = body if isinstance(body, str) else _json.dumps(body, ensure_ascii=False)
                except Exception:
                    params_text = str(body)
                if len(params_text) > 200:
                    params_text = params_text[:200] + "…"
                params_html = f"<code>{html_lib.escape(params_text)}</code>"
            else:
                params_html = "<span style='color:#94a3b8'>-</span>"

            method = r.method or ""
            endpoint = r.endpoint or "-"
            interface_html = (
                f"<code>{html_lib.escape(method)} {html_lib.escape(endpoint)}</code>"
                if method and method != "-"
                else f"<code>{html_lib.escape(endpoint)}</code>"
            )

            rows_html_parts.append(
                f"<tr>"
                f"<td>{idx}</td>"
                f"<td>{html_lib.escape(r.scenario_name or '-')}</td>"
                f"<td>{interface_html}</td>"
                f"<td>{params_html}</td>"
                f"<td>{status_code_html}</td>"
                f"<td><span class='badge {badge_cls}'>{badge_text}</span></td>"
                f"</tr>"
            )
        rows_html = "\n".join(rows_html_parts) or "<tr><td colspan='6' style='text-align:center;color:#94a3b8;padding:24px'>暂无明细数据</td></tr>"

        gen_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        title = html_lib.escape(scheduled_run.name or "定时运行报告")
        sr_identifier = html_lib.escape(scheduled_run.identifier or "")
        exec_identifier = html_lib.escape(execution_identifier or "")

        meta_parts = [
            f"<span>📅 生成时间：{gen_time}</span>",
            f"<span>🆔 执行编号：{exec_identifier}</span>",
            f"<span>🔁 任务编号：{sr_identifier}</span>",
        ]
        if environment:
            meta_parts.append(f"<span>🌐 环境：{html_lib.escape(str(environment))}</span>")
        if base_url:
            meta_parts.append(f"<span>🔗 BaseURL：{html_lib.escape(str(base_url))}</span>")
        meta_html = "\n      ".join(meta_parts)

        scenario_labels_json = _json.dumps(scenario_labels, ensure_ascii=False)
        scenario_full_labels_json = _json.dumps(full_labels, ensure_ascii=False)
        scenario_pass_json = _json.dumps(scenario_pass)
        scenario_fail_json = _json.dumps(scenario_fail)
        # 横向条形图高度自适应：每条 28px，最低 240
        scenario_chart_height = max(240, 28 * len(scenario_labels) + 80)

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} - 定时运行报告</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif; background:#f0f2f5; color:#333; padding:20px; }}
.container {{ max-width:1200px; margin:0 auto; }}
.header {{ background:linear-gradient(135deg,#1a73e8 0%,#0d47a1 100%); color:#fff; padding:36px 40px; border-radius:16px; margin-bottom:24px; }}
.header h1 {{ font-size:26px; margin-bottom:10px; }}
.header .meta {{ opacity:.92; font-size:13px; }}
.header .meta span {{ margin-right:24px; display:inline-block; }}
.summary-grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:16px; margin-bottom:24px; }}
.summary-card {{ background:#fff; border-radius:12px; padding:24px; text-align:center; box-shadow:0 2px 8px rgba(0,0,0,.06); }}
.summary-card .number {{ font-size:36px; font-weight:700; }}
.summary-card .label {{ font-size:13px; color:#666; margin-top:6px; }}
.summary-card.total .number {{ color:#1a73e8; }}
.summary-card.pass .number {{ color:#22c55e; }}
.summary-card.fail .number {{ color:#ef4444; }}
.summary-card.rate .number {{ color:#f59e0b; }}
.chart-row {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:24px; }}
.chart-card {{ background:#fff; border-radius:12px; padding:20px; box-shadow:0 2px 8px rgba(0,0,0,.06); }}
.chart-card h3 {{ font-size:15px; color:#444; margin-bottom:12px; }}
.chart-card canvas {{ max-height:none; }}
.chart-card.scenario {{ overflow-x:auto; }}
.table-card {{ background:#fff; border-radius:12px; padding:20px; box-shadow:0 2px 8px rgba(0,0,0,.06); margin-bottom:24px; }}
.table-card h3 {{ font-size:15px; color:#444; margin-bottom:16px; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th {{ background:#f8fafc; text-align:left; padding:12px 16px; font-weight:600; color:#555; border-bottom:2px solid #e2e8f0; }}
td {{ padding:10px 16px; border-bottom:1px solid #f1f5f9; vertical-align:top; word-break:break-all; }}
td code {{ font-size:12px; color:#475569; }}
.badge {{ display:inline-block; padding:2px 10px; border-radius:20px; font-size:12px; font-weight:600; }}
.badge.pass {{ background:#dcfce7; color:#16a34a; }}
.badge.fail {{ background:#fef2f2; color:#dc2626; }}
.badge.skip {{ background:#f1f5f9; color:#64748b; }}
.footer {{ text-align:center; color:#999; font-size:12px; padding:20px 0; }}
@media (max-width:768px) {{
  .summary-grid {{ grid-template-columns:repeat(2,1fr); }}
  .chart-row {{ grid-template-columns:1fr; }}
}}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>{title}</h1>
    <div class="meta">
      {meta_html}
    </div>
  </div>

  <div class="summary-grid">
    <div class="summary-card total"><div class="number">{total}</div><div class="label">总测试用例</div></div>
    <div class="summary-card pass"><div class="number">{passed}</div><div class="label">通过</div></div>
    <div class="summary-card fail"><div class="number">{failed}</div><div class="label">失败</div></div>
    <div class="summary-card rate"><div class="number">{pass_rate}%</div><div class="label">通过率</div></div>
  </div>

  <div class="chart-row">
    <div class="chart-card">
      <h3>用例通过/失败分布</h3>
      <div style="position:relative;height:300px"><canvas id="resultChart"></canvas></div>
    </div>
    <div class="chart-card scenario">
      <h3>场景覆盖分布</h3>
      <div style="position:relative;height:{scenario_chart_height}px"><canvas id="scenarioChart"></canvas></div>
    </div>
  </div>

  <div class="table-card">
    <h3>详细测试结果</h3>
    <table>
      <thead>
        <tr>
          <th style="width:50px">#</th>
          <th>场景</th>
          <th>接口</th>
          <th>请求参数</th>
          <th style="width:80px">状态码</th>
          <th style="width:80px">结果</th>
        </tr>
      </thead>
      <tbody>
{rows_html}
      </tbody>
    </table>
  </div>

  <div class="footer">定时运行报告 · 自动生成 by AI Test Agent</div>
</div>

<script>
new Chart(document.getElementById('resultChart'), {{
  type: 'doughnut',
  data: {{
    labels: ['通过 ({passed})', '失败 ({failed})'],
    datasets: [{{ data: [{passed}, {failed}], backgroundColor: ['#22c55e','#ef4444'], borderWidth: 0 }}]
  }},
  options: {{ responsive:true, maintainAspectRatio:false, plugins:{{ legend:{{ position:'bottom' }} }} }}
}});
const scenarioLabels = {scenario_labels_json};
const scenarioFullLabels = {scenario_full_labels_json};
const scenarioPass = {scenario_pass_json};
const scenarioFail = {scenario_fail_json};
new Chart(document.getElementById('scenarioChart'), {{
  type: 'bar',
  data: {{
    labels: scenarioLabels,
    datasets: [
      {{ label:'通过', data: scenarioPass, backgroundColor:'#22c55e', borderRadius:4, stack:'s' }},
      {{ label:'失败', data: scenarioFail, backgroundColor:'#ef4444', borderRadius:4, stack:'s' }}
    ]
  }},
  options: {{
    indexAxis: 'y',
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ position:'bottom' }},
      tooltip: {{
        callbacks: {{
          title: (items) => scenarioFullLabels[items[0].dataIndex] || items[0].label
        }}
      }}
    }},
    scales: {{
      x: {{ stacked:true, beginAtZero:true, ticks:{{ stepSize:1, precision:0 }} }},
      y: {{ stacked:true, ticks:{{ autoSkip:false, font:{{ size:11 }} }} }}
    }}
  }}
}});
</script>
</body>
</html>"""

        report_path = f"scheduled-run-reports/{execution_id}/report.html"
        MinIOClient.upload_bytes(
            object_name=report_path,
            data=html.encode("utf-8"),
            content_type="text/html",
        )
        return report_path
    except Exception as e:
        print(f"[scheduler] 生成报告失败: {e}")
        return None


def add_job(scheduled_run: ScheduledRun):
    """向 scheduler 添加一个 cron job"""
    job_id = f"sr_{scheduled_run.id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    if not scheduled_run.is_active:
        print(f"[scheduler] 任务 {scheduled_run.name} ({job_id}) 已禁用，跳过注册")
        return
    try:
        import pytz
        tz = pytz.timezone(scheduled_run.timezone)
    except Exception:
        import pytz
        tz = pytz.UTC

    trigger = CronTrigger.from_crontab(scheduled_run.cron_expression, timezone=tz)
    scheduler.add_job(
        _execute_scheduled_run,
        trigger=trigger,
        id=job_id,
        args=[str(scheduled_run.id)],
        replace_existing=True,
        misfire_grace_time=3600,  # 1小时容错窗口，防止短暂延迟导致 misfire
        coalesce=True,            # 错过多次触发时合并为一次执行
        max_instances=1,          # 同一任务最多同时运行1个实例
    )
    print(f"[scheduler] 注册任务: {scheduled_run.name} ({job_id}) cron={scheduled_run.cron_expression} tz={scheduled_run.timezone}")


def remove_job(scheduled_run_id: str):
    job_id = f"sr_{scheduled_run_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


async def load_all_jobs():
    """应用启动时从数据库加载所有激活的定时任务"""
    async with async_session_factory() as session:
        repo = ScheduledRunRepository(session)
        runs = await repo.get_all_active()
        for run in runs:
            add_job(run)
    print(f"[scheduler] 启动完成，已加载 {len(runs)} 个定时任务")
    # 打印所有已注册 job
    for job in scheduler.get_jobs():
        next_run = getattr(job, "next_run_time", None) or getattr(job, "next_fire_time", None)
        print(f"[scheduler]   - {job.id}: next_run={next_run}")
