"""
渗透测试 - 报告生成工具模块

提供专业的渗透测试报告生成功能，支持 Markdown、JSON 格式，
并可通过 MCP Chart Server 生成数据可视化图表。
"""

import json
import os
import re
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

from langchain_core.tools import tool

from app.config.settings import settings
from app.config.minio_client import MinIOClient

# Windows 下 subprocess 默认编码为 gbk，MCP server (npx) 输出可能包含非 ASCII 字符，
# 需强制使用 utf-8 以避免 UnicodeDecodeError。
if os.name == "nt":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

SECURITY_WORKSPACE = Path(settings.security_workspace_root).resolve()
SECURITY_WORKSPACE.mkdir(parents=True, exist_ok=True)


def _safe_filename(name: str) -> str:
    """生成安全的文件名（只保留 ASCII 字母数字、下划线和连字符）"""
    safe = re.sub(r'[^\w\-]', '_', name)
    safe = re.sub(r'_+', '_', safe)
    return safe.strip('_')


def _upload_report_to_minio(content: str, object_name: str, content_type: str = "text/html") -> str:
    """将报告内容上传到 MinIO，返回 object_name"""
    data = content.encode("utf-8")
    MinIOClient.upload_bytes(
        object_name=object_name,
        data=data,
        content_type=content_type,
    )
    return object_name


# ============================================================================
# 报告生成
# ============================================================================

@tool
async def generate_pentest_report(
    project_name: str,
    target: str,
    findings: List[Dict[str, Any]],
    tester_name: str = "AI 渗透测试智能体",
    output_format: str = "markdown",
    include_charts: bool = True,
    output_file: Optional[str] = None,
) -> str:
    """
    生成专业的渗透测试报告

    根据漏洞发现结果生成标准化的渗透测试报告，支持 Markdown 和 JSON 格式，
    并可生成风险分布图表。

    Args:
        project_name: 项目名称
        target: 测试目标（URL/IP）
        findings: 漏洞发现列表，每项应包含：
            - id: 漏洞编号（如 VL-001）
            - title: 漏洞标题
            - severity: 风险等级（Critical/High/Medium/Low/Info）
            - type: 漏洞类型
            - url: 受影响 URL
            - parameter: 受影响参数
            - description: 漏洞描述
            - reproduction: 复现步骤
            - evidence: 证据
            - remediation: 修复建议
        tester_name: 测试人员名称
        output_format: 输出格式，markdown 或 json
        include_charts: 是否包含图表（通过 MCP Chart Server 生成）
        output_file: MinIO 对象名称覆盖（可选，默认自动生成）

    Returns:
        JSON 格式的报告生成结果，包含以下字段：
        - success: 是否成功
        - object_name: MinIO 对象路径（如 "pentest/reports/20250604_120000_xxx.md"）
        - content: 报告完整内容
        - format: 报告格式
        - total_vulnerabilities: 漏洞总数
        - risk_score: 风险评分
        - severity_distribution: 风险等级分布统计

    **重要：** 返回结果中的 `object_name` 必须传给 `mgmt_save_pentest_report` 的 `file_path` 参数，
    才能在前端正确显示和下载报告。

    Example:
        >>> findings = [
        ...     {
        ...         "id": "VL-001",
        ...         "title": "SQL 注入漏洞",
        ...         "severity": "Critical",
        ...         "type": "SQL Injection",
        ...         "url": "https://example.com/search",
        ...         "parameter": "q",
        ...         "description": "搜索参数存在 SQL 注入",
        ...         "reproduction": "1. 访问 URL\n2. 输入 ' OR '1'='1",
        ...         "evidence": "页面返回所有数据",
        ...         "remediation": "使用参数化查询"
        ...     }
        ... ]
        >>> result = await generate_pentest_report(
        ...     project_name="客户系统安全评估",
        ...     target="https://example.com",
        ...     findings=findings
        ... )
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_time = datetime.now().strftime("%Y-%m-%d")

    # Calculate statistics
    severity_counts = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0, "Info": 0}
    severity_scores = {"Critical": 9.5, "High": 8.0, "Medium": 5.5, "Low": 2.0, "Info": 0.0}

    for finding in findings:
        sev = finding.get("severity", "Info")
        if sev in severity_counts:
            severity_counts[sev] += 1

    total_vulns = sum(severity_counts.values())
    risk_score = sum(severity_counts[s] * severity_scores[s] for s in severity_counts) / max(total_vulns, 1)

    # Generate chart data for MCP Chart Server
    chart_files = []
    if include_charts:
        chart_files = await _generate_charts(severity_counts, project_name, timestamp)

    ext = "md" if output_format == "markdown" else "json"
    safe_name = _safe_filename(project_name)
    object_name = f"pentest/reports/{timestamp}_{safe_name}.{ext}"

    if output_format == "json":
        report_data = {
            "project_info": {
                "project_name": project_name,
                "target": target,
                "test_date": report_time,
                "tester": tester_name,
            },
            "summary": {
                "total_vulnerabilities": total_vulns,
                "risk_score": round(risk_score, 1),
                "severity_distribution": severity_counts,
            },
            "findings": findings,
            "charts": chart_files,
        }
        content = json.dumps(report_data, ensure_ascii=False, indent=2)
        content_type = "application/json"
    else:
        # Markdown format
        content = _generate_markdown_report(
            project_name=project_name,
            target=target,
            report_time=report_time,
            tester_name=tester_name,
            findings=findings,
            severity_counts=severity_counts,
            risk_score=risk_score,
            chart_files=chart_files,
        )
        content_type = "text/markdown"

    # Upload to MinIO
    _upload_report_to_minio(content, object_name, content_type)

    return json.dumps({
        "success": True,
        "project": project_name,
        "target": target,
        "total_vulnerabilities": total_vulns,
        "risk_score": round(risk_score, 1),
        "severity_distribution": severity_counts,
        "object_name": object_name,
        "content": content,
        "charts": chart_files,
        "format": output_format,
    }, ensure_ascii=False, indent=2)


async def _generate_charts(severity_counts: Dict[str, int], project_name: str, timestamp: str) -> List[str]:
    """
    通过 MCP Chart Server 生成图表

    生成风险分布饼图和柱状图，保存为图片文件。
    图表使用 AntV 规范，通过 MCP Server 渲染。
    """
    chart_files = []
    charts_dir = SECURITY_WORKSPACE / f"charts_{timestamp}"
    charts_dir.mkdir(parents=True, exist_ok=True)

    # Since MCP Chart Server integration requires async tool call,
    # we prepare chart spec files that can be rendered later.
    # The actual rendering will be done by the agent using MCP tools.

    # Pie chart spec (AntV G2Plot)
    pie_spec = {
        "type": "pie",
        "title": f"{project_name} - 风险等级分布",
        "data": [
            {"type": "严重", "value": severity_counts["Critical"]},
            {"type": "高危", "value": severity_counts["High"]},
            {"type": "中危", "value": severity_counts["Medium"]},
            {"type": "低危", "value": severity_counts["Low"]},
            {"type": "信息", "value": severity_counts["Info"]},
        ],
        "colorField": "type",
        "angleField": "value",
        "color": ["#ff4d4f", "#faad14", "#fa8c16", "#1890ff", "#d9d9d9"],
        "radius": 0.8,
        "label": {"type": "outer"},
        "legend": {"position": "bottom"},
    }

    pie_path = charts_dir / "severity_pie.json"
    pie_path.write_text(json.dumps(pie_spec, ensure_ascii=False, indent=2), encoding="utf-8")
    chart_files.append(str(pie_path))

    # Bar chart spec (AntV G2Plot)
    bar_spec = {
        "type": "column",
        "title": f"{project_name} - 漏洞数量统计",
        "data": [
            {"severity": "严重", "count": severity_counts["Critical"]},
            {"severity": "高危", "count": severity_counts["High"]},
            {"severity": "中危", "count": severity_counts["Medium"]},
            {"severity": "低危", "count": severity_counts["Low"]},
            {"severity": "信息", "count": severity_counts["Info"]},
        ],
        "xField": "severity",
        "yField": "count",
        "colorField": "severity",
        "color": ["#ff4d4f", "#faad14", "#fa8c16", "#1890ff", "#d9d9d9"],
        "label": {"position": "top"},
        "columnStyle": {"radius": [4, 4, 0, 0]},
    }

    bar_path = charts_dir / "severity_bar.json"
    bar_path.write_text(json.dumps(bar_spec, ensure_ascii=False, indent=2), encoding="utf-8")
    chart_files.append(str(bar_path))

    # CVSS score distribution (if available)
    line_spec = {
        "type": "line",
        "title": f"{project_name} - 漏洞趋势",
        "data": [
            {"phase": "信息收集", "count": sum(severity_counts.values())},
            {"phase": "漏洞扫描", "count": severity_counts["Critical"] + severity_counts["High"]},
            {"phase": "漏洞验证", "count": severity_counts["Critical"]},
        ],
        "xField": "phase",
        "yField": "count",
        "point": {"size": 5, "shape": "diamond"},
        "label": {},
    }

    line_path = charts_dir / "trend_line.json"
    line_path.write_text(json.dumps(line_spec, ensure_ascii=False, indent=2), encoding="utf-8")
    chart_files.append(str(line_path))

    return chart_files


def _generate_markdown_report(
    project_name: str,
    target: str,
    report_time: str,
    tester_name: str,
    findings: List[Dict[str, Any]],
    severity_counts: Dict[str, int],
    risk_score: float,
    chart_files: List[str],
) -> str:
    """生成 Markdown 格式的渗透测试报告"""

    severity_icons = {
        "Critical": "🔴",
        "High": "🟠",
        "Medium": "🟡",
        "Low": "🔵",
        "Info": "⚪",
    }

    # Executive Summary
    md = f"""# 渗透测试报告

## 项目信息

| 项目 | 内容 |
|------|------|
| 项目名称 | {project_name} |
| 测试目标 | {target} |
| 测试日期 | {report_time} |
| 测试人员 | {tester_name} |

## 执行摘要

### 风险概况

- **综合风险评分**: {risk_score:.1f}/10
- **漏洞总数**: {sum(severity_counts.values())}
- **严重漏洞**: {severity_counts['Critical']}
- **高危漏洞**: {severity_counts['High']}
- **中危漏洞**: {severity_counts['Medium']}
- **低危漏洞**: {severity_counts['Low']}
- **信息级**: {severity_counts['Info']}

### 风险等级分布

| 等级 | 数量 | 图标 |
|------|------|------|
| 严重 | {severity_counts['Critical']} | 🔴 |
| 高危 | {severity_counts['High']} | 🟠 |
| 中危 | {severity_counts['Medium']} | 🟡 |
| 低危 | {severity_counts['Low']} | 🔵 |
| 信息 | {severity_counts['Info']} | ⚪ |

## 漏洞详情

"""

    # Add each finding
    for finding in findings:
        sev = finding.get("severity", "Info")
        icon = severity_icons.get(sev, "⚪")
        md += f"""### {icon} {finding.get('id', 'VL-XXX')} - {finding.get('title', '未命名漏洞')}

**风险等级**: {sev}

**漏洞类型**: {finding.get('type', '未知')}

**受影响 URL**: {finding.get('url', 'N/A')}

**受影响参数**: {finding.get('parameter', 'N/A')}

#### 描述

{finding.get('description', '无描述')}

#### 复现步骤

{finding.get('reproduction', '无复现步骤')}

#### 证据

```
{finding.get('evidence', '无证据')}
```

#### 修复建议

{finding.get('remediation', '无修复建议')}

---

"""

    # Appendices
    md += """## 附录

### 风险等级定义

| 等级 | CVSS | 描述 |
|------|------|------|
| 严重 | 9.0-10.0 | 可直接获取服务器权限，需立即修复 |
| 高危 | 7.0-8.9 | 可获取敏感数据，需尽快修复 |
| 中危 | 4.0-6.9 | 需特定条件利用，建议修复 |
| 低危 | 0.1-3.9 | 影响轻微，建议修复 |
| 信息 | 0.0 | 信息泄露，需关注 |

### 免责声明

本报告仅供授权的安全评估使用。未经书面许可，不得将本报告用于任何其他目的。
"""

    return md


@tool
async def generate_executive_summary(
    project_name: str,
    target: str,
    findings: List[Dict[str, Any]],
    tester_name: str = "AI 渗透测试智能体",
    output_file: Optional[str] = None,
) -> str:
    """
    生成执行摘要（管理层视角）

    生成简洁的执行摘要报告，适合管理层阅读。

    Args:
        project_name: 项目名称
        target: 测试目标
        findings: 漏洞发现列表
        tester_name: 测试人员名称
        output_file: MinIO 对象名称覆盖（可选）

    Returns:
        JSON 格式的报告生成结果
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_time = datetime.now().strftime("%Y-%m-%d")

    # Calculate statistics
    severity_counts = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0, "Info": 0}
    for finding in findings:
        sev = finding.get("severity", "Info")
        if sev in severity_counts:
            severity_counts[sev] += 1

    total_vulns = sum(severity_counts.values())
    risk_score = sum(severity_counts[s] * {"Critical": 9.5, "High": 8.0, "Medium": 5.5, "Low": 2.0, "Info": 0.0}[s] for s in severity_counts) / max(total_vulns, 1)

    safe_name = _safe_filename(project_name)
    object_name = output_file or f"pentest/reports/{timestamp}_{safe_name}_executive.md"

    content = f"""# 渗透测试执行摘要

## 项目概况

| 项目 | 内容 |
|------|------|
| 项目名称 | {project_name} |
| 测试目标 | {target} |
| 测试日期 | {report_time} |
| 测试人员 | {tester_name} |

## 风险评估

### 综合评分

**风险评分: {risk_score:.1f}/10**

### 漏洞统计

| 等级 | 数量 | 状态 |
|------|------|------|
| 🔴 严重 | {severity_counts['Critical']} | {'需立即处理' if severity_counts['Critical'] > 0 else '无'} |
| 🟠 高危 | {severity_counts['High']} | {'需尽快处理' if severity_counts['High'] > 0 else '无'} |
| 🟡 中危 | {severity_counts['Medium']} | {'建议处理' if severity_counts['Medium'] > 0 else '无'} |
| 🔵 低危 | {severity_counts['Low']} | {'可后续处理' if severity_counts['Low'] > 0 else '无'} |
| ⚪ 信息 | {severity_counts['Info']} | {'需关注' if severity_counts['Info'] > 0 else '无'} |

## 关键发现

"""

    # Add critical and high findings
    critical_high = [f for f in findings if f.get("severity") in ("Critical", "High")]
    if critical_high:
        content += "### 需优先处理的漏洞\n\n"
        for finding in critical_high:
            content += f"- **{finding.get('id', 'VL-XXX')}**: {finding.get('title')} ({finding.get('severity')})\n"
    else:
        content += "未发现严重或高危漏洞。\n"

    content += """
## 建议措施

1. **立即修复**所有严重漏洞
2. **优先处理**高危漏洞
3. **制定计划**修复中危漏洞
4. **定期复查**低危和信息级问题

## 合规声明

本测试在获得明确授权的情况下进行，所有发现的漏洞仅供被测方修复使用。
"""

    # Upload to MinIO
    _upload_report_to_minio(content, object_name, "text/markdown")

    return json.dumps({
        "success": True,
        "project": project_name,
        "total_vulnerabilities": total_vulns,
        "risk_score": round(risk_score, 1),
        "severity_distribution": severity_counts,
        "object_name": object_name,
        "content": content,
        "format": "markdown",
    }, ensure_ascii=False, indent=2)


@tool
async def generate_html_pentest_report(
    project_name: str,
    target: str,
    findings: List[Dict[str, Any]],
    tester_name: str = "AI 渗透测试智能体",
    output_file: Optional[str] = None,
) -> str:
    """
    生成 HTML 格式的渗透测试报告

    生成专业的 HTML 图文报告，自动内嵌图表（CSS 图表回退）。

    Args:
        project_name: 项目名称
        target: 测试目标
        findings: 漏洞发现列表
        tester_name: 测试人员名称
        output_file: MinIO 对象名称覆盖（可选）

    Returns:
        JSON 格式的报告生成结果

    **重要：** 返回结果中的 `object_name` 必须传给 `mgmt_save_pentest_report` 的 `file_path` 参数。
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_time = datetime.now().strftime("%Y-%m-%d")

    # Calculate statistics
    severity_counts = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0, "Info": 0}
    severity_colors = {
        "Critical": "#ff4d4f",
        "High": "#faad14",
        "Medium": "#fa8c16",
        "Low": "#1890ff",
        "Info": "#d9d9d9",
    }
    severity_labels_cn = {
        "Critical": "严重",
        "High": "高危",
        "Medium": "中危",
        "Low": "低危",
        "Info": "信息",
    }
    for finding in findings:
        sev = finding.get("severity", "Info")
        if sev in severity_counts:
            severity_counts[sev] += 1

    total_vulns = sum(severity_counts.values())
    risk_score = sum(severity_counts[s] * {"Critical": 9.5, "High": 8.0, "Medium": 5.5, "Low": 2.0, "Info": 0.0}[s] for s in severity_counts) / max(total_vulns, 1)

    safe_name = _safe_filename(project_name)
    object_name = output_file or f"pentest/reports/{timestamp}_{safe_name}.html"

    # Build severity bar chart with CSS
    max_count = max(severity_counts.values()) if severity_counts.values() else 1
    bars_html = ""
    for sev, count in severity_counts.items():
        width = (count / max_count * 100) if max_count > 0 else 0
        bars_html += f"""
        <div class="bar-row">
            <span class="bar-label">{severity_labels_cn[sev]}</span>
            <div class="bar-container">
                <div class="bar" style="width: {width}%; background: {severity_colors[sev]};"></div>
            </div>
            <span class="bar-value">{count}</span>
        </div>
        """

    # Build findings HTML
    findings_html = ""
    for finding in findings:
        sev = finding.get("severity", "Info")
        color = severity_colors.get(sev, "#d9d9d9")

        # 构建属性信息行
        meta_rows = ""
        if finding.get('type'):
            meta_rows += f'<p><strong>类型:</strong> {finding.get("type")}</p>'
        if finding.get('url'):
            meta_rows += f'<p><strong>URL:</strong> <code>{finding.get("url")}</code></p>'
        if finding.get('parameter'):
            meta_rows += f'<p><strong>参数:</strong> <code>{finding.get("parameter")}</code></p>'
        if finding.get('cvss_score'):
            meta_rows += f'<p><strong>CVSS 评分:</strong> {finding.get("cvss_score")}</p>'

        # 构建详情区块（只在有内容时显示）
        desc_block = ""
        if finding.get('description'):
            desc_block = f"""
                <div class="detail-section">
                    <h4>描述</h4>
                    <div class="detail-content">{finding.get('description')}</div>
                </div>"""

        repro_block = ""
        if finding.get('reproduction'):
            repro_block = f"""
                <div class="detail-section">
                    <h4>复现步骤</h4>
                    <pre class="reproduction">{finding.get('reproduction')}</pre>
                </div>"""

        evidence_block = ""
        if finding.get('evidence'):
            evidence_block = f"""
                <div class="detail-section">
                    <h4>证据</h4>
                    <pre class="evidence">{finding.get('evidence')}</pre>
                </div>"""

        remediation_block = ""
        if finding.get('remediation'):
            remediation_block = f"""
                <div class="detail-section">
                    <h4>修复建议</h4>
                    <div class="detail-content remediation">{finding.get('remediation')}</div>
                </div>"""

        findings_html += f"""
        <div class="finding">
            <div class="finding-header" style="border-left-color: {color};">
                <span class="finding-id">{finding.get('id', 'VL-XXX')}</span>
                <span class="finding-title">{finding.get('title', '未命名漏洞')}</span>
                <span class="finding-severity" style="background: {color};">{severity_labels_cn[sev]}</span>
            </div>
            <div class="finding-body">
                <div class="finding-meta">
                    {meta_rows}
                </div>
                {desc_block}
                {repro_block}
                {evidence_block}
                {remediation_block}
            </div>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>渗透测试报告 - {project_name}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
            padding: 20px;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            padding: 40px;
        }}
        h1 {{ color: #1a1a1a; margin-bottom: 10px; text-align: center; }}
        h2 {{ color: #333; margin: 30px 0 15px; border-bottom: 2px solid #e8e8e8; padding-bottom: 10px; }}
        h3 {{ color: #555; margin: 20px 0 10px; }}
        h4 {{ color: #666; margin: 15px 0 8px; }}
        .info-table {{
            width: 100%;
            border-collapse: collapse;
            margin: 15px 0;
        }}
        .info-table td {{
            padding: 10px;
            border: 1px solid #e8e8e8;
        }}
        .info-table td:first-child {{
            background: #f8f8f8;
            font-weight: bold;
            width: 150px;
        }}
        .summary-stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin: 20px 0;
        }}
        .stat-card {{
            background: #f8f8f8;
            border-radius: 8px;
            padding: 20px;
            text-align: center;
        }}
        .stat-value {{
            font-size: 2em;
            font-weight: bold;
            color: #ff4d4f;
        }}
        .stat-label {{
            color: #666;
            margin-top: 5px;
        }}
        .chart-container {{
            margin: 20px 0;
        }}
        .bar-row {{
            display: flex;
            align-items: center;
            margin: 8px 0;
        }}
        .bar-label {{
            width: 80px;
            font-weight: bold;
        }}
        .bar-container {{
            flex: 1;
            height: 24px;
            background: #f0f0f0;
            border-radius: 4px;
            overflow: hidden;
            margin: 0 10px;
        }}
        .bar {{
            height: 100%;
            border-radius: 4px;
            transition: width 0.3s ease;
        }}
        .bar-value {{
            width: 40px;
            text-align: right;
            font-weight: bold;
        }}
        .finding {{
            margin: 20px 0;
            border: 1px solid #e8e8e8;
            border-radius: 8px;
            overflow: hidden;
        }}
        .finding-header {{
            background: #f8f8f8;
            padding: 15px;
            display: flex;
            align-items: center;
            gap: 10px;
            border-left: 4px solid;
        }}
        .finding-id {{
            font-weight: bold;
            color: #666;
        }}
        .finding-title {{
            flex: 1;
            font-weight: bold;
        }}
        .finding-severity {{
            padding: 4px 12px;
            border-radius: 4px;
            color: white;
            font-size: 0.85em;
            font-weight: bold;
        }}
        .finding-body {{
            padding: 15px;
        }}
        .finding-body p {{
            margin: 6px 0;
        }}
        .finding-body pre {{
            background: #f5f5f5;
            padding: 12px;
            border-radius: 4px;
            overflow-x: auto;
            font-size: 0.9em;
            white-space: pre-wrap;
            word-wrap: break-word;
            line-height: 1.6;
        }}
        .finding-meta {{
            background: #fafafa;
            border-radius: 6px;
            padding: 12px 15px;
            margin-bottom: 15px;
        }}
        .finding-meta p {{
            margin: 4px 0;
            font-size: 0.9em;
        }}
        .finding-meta code {{
            background: #f0f0f0;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Consolas', 'Monaco', monospace;
            font-size: 0.85em;
            color: #d63384;
        }}
        .detail-section {{
            margin: 15px 0;
            padding: 12px 15px;
            background: #fafafa;
            border-radius: 6px;
            border-left: 3px solid #e8e8e8;
        }}
        .detail-section h4 {{
            margin: 0 0 10px 0;
            color: #333;
            font-size: 0.95em;
        }}
        .detail-content {{
            line-height: 1.7;
            color: #444;
        }}
        .detail-content.remediation {{
            background: #f0f9ff;
            border-left: 3px solid #1890ff;
            padding: 10px 14px;
            border-radius: 0 4px 4px 0;
        }}
        pre.reproduction {{
            background: #fff7e6;
            border: 1px solid #ffd591;
            border-left: 3px solid #fa8c16;
        }}
        pre.evidence {{
            background: #f6ffed;
            border: 1px solid #b7eb8f;
            border-left: 3px solid #52c41a;
        }}
        .risk-score {{
            font-size: 3em;
            font-weight: bold;
            text-align: center;
            color: #ff4d4f;
            margin: 20px 0;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>渗透测试报告</h1>

        <h2>项目信息</h2>
        <table class="info-table">
            <tr><td>项目名称</td><td>{project_name}</td></tr>
            <tr><td>测试目标</td><td>{target}</td></tr>
            <tr><td>测试日期</td><td>{report_time}</td></tr>
            <tr><td>测试人员</td><td>{tester_name}</td></tr>
        </table>

        <h2>风险评估</h2>
        <div class="risk-score">{risk_score:.1f}/10</div>

        <h2>漏洞统计</h2>
        <div class="summary-stats">
            <div class="stat-card">
                <div class="stat-value">{total_vulns}</div>
                <div class="stat-label">漏洞总数</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" style="color: #ff4d4f;">{severity_counts['Critical']}</div>
                <div class="stat-label">严重</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" style="color: #faad14;">{severity_counts['High']}</div>
                <div class="stat-label">高危</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" style="color: #fa8c16;">{severity_counts['Medium']}</div>
                <div class="stat-label">中危</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" style="color: #1890ff;">{severity_counts['Low']}</div>
                <div class="stat-label">低危</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" style="color: #999;">{severity_counts['Info']}</div>
                <div class="stat-label">信息</div>
            </div>
        </div>

        <h3>风险分布</h3>
        <div class="chart-container">
            {bars_html}
        </div>

        <h2>漏洞详情</h2>
        {findings_html}

        <h2>附录</h2>
        <h3>风险等级定义</h3>
        <table class="info-table">
            <tr><td>严重</td><td>可直接获取服务器权限，需立即修复</td></tr>
            <tr><td>高危</td><td>可获取敏感数据，需尽快修复</td></tr>
            <tr><td>中危</td><td>需特定条件利用，建议修复</td></tr>
            <tr><td>低危</td><td>影响轻微，建议修复</td></tr>
            <tr><td>信息</td><td>信息泄露，需关注</td></tr>
        </table>
    </div>
</body>
</html>
"""

    # Upload to MinIO
    _upload_report_to_minio(html, object_name, "text/html")

    return json.dumps({
        "success": True,
        "project": project_name,
        "target": target,
        "total_vulnerabilities": total_vulns,
        "risk_score": round(risk_score, 1),
        "severity_distribution": severity_counts,
        "object_name": object_name,
        "content": html,
        "format": "html",
    }, ensure_ascii=False, indent=2)
