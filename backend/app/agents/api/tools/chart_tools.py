"""
API 测试报告图表生成工具

基于 AntV CLI（@antv/cli）生成测试报告图表，
支持通过 skills + CLI 方式将测试结果数据可视化为图表。
参考: https://github.com/antvis/chart-visualization-skills
"""

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import UUID

from langchain_core.tools import tool
from sqlalchemy import select

from app.config import settings
from app.config.database import async_session_factory
from app.config.minio_client import MinIOClient
from app.models.api_endpoint import APIEndpoint
from app.models.attachment import Attachment, AttachmentEntityType

# ============================================================================
# 图表类型常量
# ============================================================================

CHART_TYPES = {
    "bar": "柱状图 - 展示各端点测试通过/失败数量对比",
    "pie": "饼图 - 展示测试结果通过/失败/跳过比例",
    "line": "折线图 - 展示测试执行时间趋势",
    "column": "条形图 - 展示测试覆盖率",
}


def _build_chart_spec(chart_type: str, data: list, title: str, x_field: str, y_field: str) -> dict:
    """
    构建 AntV G2 图表规格（JSON spec）

    Args:
        chart_type: 图表类型 (bar/pie/line/column)
        data: 图表数据列表
        title: 图表标题
        x_field: X 轴字段名
        y_field: Y 轴字段名

    Returns:
        G2 图表规格字典
    """
    base_spec = {
        "type": chart_type,
        "data": data,
        "title": {"text": title},
        "width": 800,
        "height": 400,
    }

    if chart_type == "pie":
        base_spec.update({
            "encode": {
                "value": y_field,
                "color": x_field,
            },
            "legend": {"color": {"position": "right"}},
        })
    else:
        base_spec.update({
            "encode": {
                "x": x_field,
                "y": y_field,
                "color": x_field,
            },
            "axis": {
                "x": {"title": x_field},
                "y": {"title": y_field},
            },
        })

    return base_spec


def _generate_chart_html(chart_spec: dict, title: str) -> str:
    """
    生成包含 AntV G2 图表的 HTML 页面

    Args:
        chart_spec: G2 图表规格
        title: 页面标题

    Returns:
        HTML 字符串
    """
    spec_json = json.dumps(chart_spec, ensure_ascii=False, indent=2)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <script src="https://unpkg.com/@antv/g2@5/dist/g2.min.js"></script>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 20px; background: #f5f5f5; }}
    .chart-container {{ background: white; border-radius: 8px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); margin-bottom: 20px; }}
    h1 {{ color: #333; font-size: 20px; margin-bottom: 16px; }}
    #chart {{ width: 100%; }}
  </style>
</head>
<body>
  <div class="chart-container">
    <h1>{title}</h1>
    <div id="chart"></div>
  </div>
  <script>
    const spec = {spec_json};
    const chart = new G2.Chart({{
      container: 'chart',
      ...spec,
    }});
    chart.options(spec);
    chart.render();
  </script>
</body>
</html>"""


def _try_cli_render(chart_spec: dict, output_path: str) -> bool:
    """
    尝试使用 @antv/cli 渲染图表为图片

    Args:
        chart_spec: G2 图表规格
        output_path: 输出文件路径（.png）

    Returns:
        是否成功
    """
    try:
        is_windows = sys.platform == "win32"
        spec_json = json.dumps(chart_spec)

        # 尝试使用 npx @antv/cli 渲染
        cmd = f'npx @antv/cli render --spec "{spec_json}" --output "{output_path}"'
        result = subprocess.run(
            cmd,
            shell=is_windows,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=60,
        )
        return result.returncode == 0 and Path(output_path).exists()
    except Exception:
        return False


@tool
async def generate_test_result_chart(
    endpoint_id: str,
    project_identifier: str,
    chart_type: str = "bar",
    passed: int = 0,
    failed: int = 0,
    skipped: int = 0,
    title: Optional[str] = None,
) -> str:
    """
    为 API 测试结果生成图表并保存到 MinIO

    根据测试通过/失败/跳过数量生成可视化图表（HTML 格式），
    使用 AntV G2 渲染，支持柱状图和饼图。

    Args:
        endpoint_id: API 端点 ID
        project_identifier: 项目标识符
        chart_type: 图表类型 (bar/pie)，默认 bar
        passed: 通过的测试数量
        failed: 失败的测试数量
        skipped: 跳过的测试数量
        title: 图表标题（可选，默认自动生成）

    Returns:
        JSON 格式的结果，包含 attachment_id 和 chart_url

    Example:
        >>> result = await generate_test_result_chart(
        ...     endpoint_id="5ea81a5f-c97b-4a36-a680-13637f1b9eed",
        ...     project_identifier="PR-3",
        ...     chart_type="pie",
        ...     passed=8,
        ...     failed=2,
        ...     skipped=1,
        ... )
    """
    try:
        endpoint_uuid = UUID(endpoint_id)
    except (ValueError, AttributeError):
        return json.dumps({"success": False, "error": f"Invalid endpoint_id: {endpoint_id}"}, ensure_ascii=False)

    async with async_session_factory() as session:
        endpoint_result = await session.execute(
            select(APIEndpoint).where(APIEndpoint.id == endpoint_uuid)
        )
        endpoint = endpoint_result.scalar_one_or_none()
        if not endpoint:
            return json.dumps({"success": False, "error": f"Endpoint {endpoint_id} not found"}, ensure_ascii=False)

        chart_title = title or f"{endpoint.display_name} 测试结果"
        data = [
            {"status": "通过", "count": passed},
            {"status": "失败", "count": failed},
            {"status": "跳过", "count": skipped},
        ]

        chart_spec = _build_chart_spec(
            chart_type=chart_type if chart_type in ("bar", "pie") else "bar",
            data=data,
            title=chart_title,
            x_field="status",
            y_field="count",
        )
        html_content = _generate_chart_html(chart_spec, chart_title)
        html_bytes = html_content.encode("utf-8")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        object_name = f"api-tests/{project_identifier}/endpoints/{endpoint_id}/chart-result-{timestamp}.html"

        MinIOClient.upload_bytes(
            object_name=object_name,
            data=html_bytes,
            content_type="text/html",
        )

        existing_result = await session.execute(
            select(Attachment).where(Attachment.object_name == object_name)
        )
        existing = existing_result.scalar_one_or_none()

        total = passed + failed + skipped
        description = f"测试结果图表 - {chart_title}（通过:{passed} 失败:{failed} 跳过:{skipped} 共:{total}）"

        if existing:
            existing.file_size = len(html_bytes)
            existing.description = description
            existing.updated_at = datetime.now()
            attachment = existing
        else:
            attachment = Attachment(
                entity_type=AttachmentEntityType.API_TEST_REPORT,
                entity_id=endpoint_uuid,
                project_id=endpoint.project_id,
                file_name=f"chart-result-{timestamp}.html",
                file_size=len(html_bytes),
                content_type="text/html",
                object_name=object_name,
                description=description,
                created_by="api-agent",
            )
            session.add(attachment)

        await session.commit()
        await session.refresh(attachment)

        return json.dumps({
            "success": True,
            "attachment_id": str(attachment.id),
            "object_name": object_name,
            "chart_type": chart_type,
            "summary": {"passed": passed, "failed": failed, "skipped": skipped, "total": total},
            "message": f"测试结果图表已生成并保存（{chart_type}）",
        }, ensure_ascii=False, indent=2)


@tool
async def generate_endpoint_comparison_chart(
    project_identifier: str,
    endpoint_stats: list[dict],
    chart_type: str = "bar",
    title: Optional[str] = None,
) -> str:
    """
    生成多个端点测试结果对比图表并保存到 MinIO

    将多个 API 端点的测试统计数据可视化为对比图表。

    Args:
        project_identifier: 项目标识符
        endpoint_stats: 端点统计列表，每项包含：
            - name: 端点名称
            - passed: 通过数
            - failed: 失败数
            - total: 总数
        chart_type: 图表类型 (bar/column)，默认 bar
        title: 图表标题（可选）

    Returns:
        JSON 格式的结果，包含 object_name

    Example:
        >>> result = await generate_endpoint_comparison_chart(
        ...     project_identifier="PR-3",
        ...     endpoint_stats=[
        ...         {"name": "GET /users", "passed": 5, "failed": 1, "total": 6},
        ...         {"name": "POST /login", "passed": 3, "failed": 0, "total": 3},
        ...     ],
        ...     chart_type="bar",
        ... )
    """
    if not endpoint_stats:
        return json.dumps({"success": False, "error": "endpoint_stats 不能为空"}, ensure_ascii=False)

    chart_title = title or f"{project_identifier} 端点测试对比"

    # 构建通过率数据
    data = [
        {
            "endpoint": stat.get("name", f"端点{i+1}"),
            "通过": stat.get("passed", 0),
            "失败": stat.get("failed", 0),
        }
        for i, stat in enumerate(endpoint_stats)
    ]

    chart_spec = _build_chart_spec(
        chart_type="interval" if chart_type == "bar" else "interval",
        data=data,
        title=chart_title,
        x_field="endpoint",
        y_field="通过",
    )
    # 多系列分组柱状图
    chart_spec["encode"] = {"x": "endpoint", "y": "通过", "color": "endpoint"}

    html_content = _generate_chart_html(chart_spec, chart_title)
    html_bytes = html_content.encode("utf-8")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    object_name = f"api-tests/{project_identifier}/charts/endpoint-comparison-{timestamp}.html"

    MinIOClient.upload_bytes(
        object_name=object_name,
        data=html_bytes,
        content_type="text/html",
    )

    return json.dumps({
        "success": True,
        "object_name": object_name,
        "chart_type": chart_type,
        "endpoint_count": len(endpoint_stats),
        "message": f"端点对比图表已生成（{len(endpoint_stats)} 个端点）",
    }, ensure_ascii=False, indent=2)


@tool
async def generate_test_trend_chart(
    endpoint_id: str,
    project_identifier: str,
    run_history: list[dict],
    title: Optional[str] = None,
) -> str:
    """
    生成测试执行趋势折线图并保存到 MinIO

    将历史测试运行数据可视化为趋势图，展示测试通过率随时间的变化。

    Args:
        endpoint_id: API 端点 ID
        project_identifier: 项目标识符
        run_history: 历史运行记录列表，每项包含：
            - date: 日期字符串（如 "2024-01-15"）
            - passed: 通过数
            - failed: 失败数
            - duration: 执行时长（秒）
        title: 图表标题（可选）

    Returns:
        JSON 格式的结果，包含 attachment_id

    Example:
        >>> result = await generate_test_trend_chart(
        ...     endpoint_id="5ea81a5f-c97b-4a36-a680-13637f1b9eed",
        ...     project_identifier="PR-3",
        ...     run_history=[
        ...         {"date": "2024-01-10", "passed": 5, "failed": 2, "duration": 12.3},
        ...         {"date": "2024-01-11", "passed": 6, "failed": 1, "duration": 10.5},
        ...     ],
        ... )
    """
    try:
        endpoint_uuid = UUID(endpoint_id)
    except (ValueError, AttributeError):
        return json.dumps({"success": False, "error": f"Invalid endpoint_id: {endpoint_id}"}, ensure_ascii=False)

    if not run_history:
        return json.dumps({"success": False, "error": "run_history 不能为空"}, ensure_ascii=False)

    async with async_session_factory() as session:
        endpoint_result = await session.execute(
            select(APIEndpoint).where(APIEndpoint.id == endpoint_uuid)
        )
        endpoint = endpoint_result.scalar_one_or_none()
        if not endpoint:
            return json.dumps({"success": False, "error": f"Endpoint {endpoint_id} not found"}, ensure_ascii=False)

        chart_title = title or f"{endpoint.display_name} 测试趋势"

        # 计算通过率
        data = []
        for run in run_history:
            total = run.get("passed", 0) + run.get("failed", 0)
            pass_rate = round(run.get("passed", 0) / total * 100, 1) if total > 0 else 0
            data.append({
                "date": run.get("date", ""),
                "通过率(%)": pass_rate,
                "执行时长(s)": run.get("duration", 0),
            })

        chart_spec = _build_chart_spec(
            chart_type="line",
            data=data,
            title=chart_title,
            x_field="date",
            y_field="通过率(%)",
        )

        html_content = _generate_chart_html(chart_spec, chart_title)
        html_bytes = html_content.encode("utf-8")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        object_name = f"api-tests/{project_identifier}/endpoints/{endpoint_id}/chart-trend-{timestamp}.html"

        MinIOClient.upload_bytes(
            object_name=object_name,
            data=html_bytes,
            content_type="text/html",
        )

        attachment = Attachment(
            entity_type=AttachmentEntityType.API_TEST_REPORT,
            entity_id=endpoint_uuid,
            project_id=endpoint.project_id,
            file_name=f"chart-trend-{timestamp}.html",
            file_size=len(html_bytes),
            content_type="text/html",
            object_name=object_name,
            description=f"测试趋势图表 - {chart_title}（{len(run_history)} 次运行记录）",
            created_by="api-agent",
        )
        session.add(attachment)
        await session.commit()
        await session.refresh(attachment)

        return json.dumps({
            "success": True,
            "attachment_id": str(attachment.id),
            "object_name": object_name,
            "data_points": len(run_history),
            "message": f"测试趋势图表已生成（{len(run_history)} 个数据点）",
        }, ensure_ascii=False, indent=2)
