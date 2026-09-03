"""
Web 测试报告 Markdown 转 HTML 可视化工具

将已有的 Markdown 测试报告转换为带有图表的 HTML 可视化报告，
并保存到 MinIO 和数据库中，让前端可以展示。
"""

import json
import re
from datetime import datetime
from pathlib import Path
from uuid import UUID

from app.config.minio_client import MinIOClient
from app.config.database import async_session_factory
from app.models.attachment import Attachment, AttachmentEntityType
from app.models.web_function import WebSubFunction
from app.models.project import Project


class MarkdownReportConverter:
    """将 Markdown 测试报告转换为可视化 HTML 报告"""

    def __init__(self, project_identifier: str = "PR-2"):
        self.project_identifier = project_identifier
        self.project_id = None

    async def _get_project_id(self) -> UUID:
        """获取项目 ID"""
        async with async_session_factory() as session:
            stmt = select(Project).where(Project.identifier == self.project_identifier)
            result = await session.execute(stmt)
            project = result.scalar_one_or_none()
            if not project:
                raise ValueError(f"项目 {self.project_identifier} 不存在")
            self.project_id = project.id
            return project.id

    def parse_markdown_report(self, md_content: str) -> dict:
        """
        解析 Markdown 测试报告内容
        """
        data = {
            "run_id": "",
            "execution_time": "",
            "duration": "",
            "script": "",
            "framework": "",
            "environment": "",
            "account": "",
            "overall_result": "",
            "pass_rate": 0,
            "stats": {"passed": 0, "skipped": 0, "failed": 0, "total": 0},
            "modules": [],
            "coverage": [],
            "defects": {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0},
            "screenshots": [],
            "conclusion": "",
        }

        # 解析运行编号
        m = re.search(r'\*\*运行编号\*\*\s*\|\s*(\S+)', md_content)
        if m:
            data["run_id"] = m.group(1)

        # 解析执行时间
        m = re.search(r'\*\*执行时间\*\*\s*\|\s*([^\n]+)', md_content)
        if m:
            data["execution_time"] = m.group(1).strip()

        # 解析执行时长
        m = re.search(r'\*\*执行时长\*\*\s*\|\s*([^\n]+)', md_content)
        if m:
            data["duration"] = m.group(1).strip()

        # 解析测试脚本
        m = re.search(r'\*\*测试脚本\*\*\s*\|\s*([^\n]+)', md_content)
        if m:
            data["script"] = m.group(1).strip()

        # 解析测试框架
        m = re.search(r'\*\*测试框架\*\*\s*\|\s*([^\n]+)', md_content)
        if m:
            data["framework"] = m.group(1).strip()

        # 解析测试环境
        m = re.search(r'\*\*测试环境\*\*\s*\|\s*([^\n]+)', md_content)
        if m:
            data["environment"] = m.group(1).strip()

        # 解析测试账号
        m = re.search(r'\*\*测试账号\*\*\s*\|\s*([^\n]+)', md_content)
        if m:
            data["account"] = m.group(1).strip()

        # 解析执行结果
        m = re.search(r'\*\*执行结果\*\*\s*\|\s*([^\n]+)', md_content)
        if m:
            data["overall_result"] = m.group(1).strip()

        # 解析通过率
        m = re.search(r'\*\*通过率\*\*\s*\|\s*(\d+)%', md_content)
        if m:
            data["pass_rate"] = int(m.group(1))

        # 解析整体统计 - 使用更健壮的方式
        stats_section_start = md_content.find("### 整体统计")
        if stats_section_start != -1:
            next_section = md_content.find("\n\n---", stats_section_start)
            if next_section == -1:
                next_section = md_content.find("\n## ", stats_section_start)
            if next_section == -1:
                next_section = len(md_content)
            stats_section = md_content[stats_section_start:next_section]
            for line in stats_section.split('\n'):
                line = line.strip()
                if not line.startswith('|') or '指标' in line or '---' in line or '-:' in line:
                    continue
                parts = [p.strip() for p in line.split('|')]
                # parts like ['', '指标', '数量', '']
                if len(parts) >= 3 and parts[1] and parts[1] != '指标':
                    label = parts[1]
                    value = parts[2].replace('*', '').replace('**', '')
                    if '通过' in label and '合计' not in label:
                        try:
                            data["stats"]["passed"] = int(value)
                        except:
                            pass
                    elif '跳过' in label:
                        try:
                            data["stats"]["skipped"] = int(value)
                        except:
                            pass
                    elif '失败' in label:
                        try:
                            data["stats"]["failed"] = int(value)
                        except:
                            pass
                    elif '合计' in label:
                        try:
                            data["stats"]["total"] = int(value)
                        except:
                            pass

        # 解析各模块测试结果
        # 分两步：先找到模块头，然后提取步骤和小计
        module_headers = re.findall(r'###\s+([^\n]+?)\s+\((WSF-\d+)\)', md_content)
        print(f"[DEBUG] Found {len(module_headers)} module headers: {module_headers}")

        for module_name, wsf_id in module_headers:
            module_name = module_name.strip()
            # 找到该模块在文本中的位置
            module_start = md_content.find(f"### {module_name} ({wsf_id})")
            if module_start == -1:
                continue

            # 找到下一个模块或章节结束的位置
            next_module = len(md_content)
            for next_name, next_wsf in module_headers:
                if next_name != module_name:
                    pos = md_content.find(f"### {next_name} ({next_wsf})", module_start + 1)
                    if pos != -1 and pos < next_module:
                        next_module = pos

            module_section = md_content[module_start:next_module]

            # 提取步骤行（表格数据行）- 只提取到小计之前的行
            steps = []
            in_steps = False
            for line in module_section.split('\n'):
                line = line.strip()
                # 跳过空行和分隔线
                if not line:
                    continue
                # 遇到小计行就停止
                if line.startswith('**小计**'):
                    break
                # 跳过表格头部和分隔行
                if not line.startswith('|') or '步骤' in line or '-:' in line or '---' in line:
                    continue
                parts = [p.strip() for p in line.split('|')]
                # parts like ['', '1', '页面加载', '通过', '说明', '']
                if len(parts) >= 5 and parts[1] and parts[1] != '步骤' and parts[1].isdigit():
                    step_num = parts[1]
                    operation = parts[2]
                    result = parts[3]
                    note = parts[4] if len(parts) > 4 else ""
                    steps.append({
                        "step": step_num,
                        "operation": operation,
                        "result": result,
                        "note": note,
                        "status": "pass" if "通过" in result else "skip" if "跳过" in result else "fail"
                    })

            # 解析小计
            passed = 0
            skipped = 0
            failed = 0
            summary_match = re.search(r'\*\*小计\*\*\s*:\s*([^\n]+)', module_section)
            if summary_match:
                summary = summary_match.group(1)
                pm = re.search(r'[✅✓√]\s*(\d+)', summary)
                if pm:
                    passed = int(pm.group(1))
                sm = re.search(r'[↪️]\s*(\d+)', summary)
                if sm:
                    skipped = int(sm.group(1))
                fm = re.search(r'[❌✘×]\s*(\d+)', summary)
                if fm:
                    failed = int(fm.group(1))

            data["modules"].append({
                "name": module_name,
                "wsf_id": wsf_id,
                "steps": steps,
                "passed": passed,
                "skipped": skipped,
                "failed": failed,
                "total": passed + skipped + failed
            })

        # 解析测试覆盖分析 - 使用更健壮的方式
        coverage_section_start = md_content.find("## 三、测试覆盖分析")
        if coverage_section_start != -1:
            next_section = md_content.find("\n## ", coverage_section_start + 1)
            if next_section == -1:
                next_section = len(md_content)
            coverage_section = md_content[coverage_section_start:next_section]
            for line in coverage_section.split('\n'):
                line = line.strip()
                if not line.startswith('|') or '模块' in line or '---' in line or '-:' in line:
                    continue
                parts = [p.strip() for p in line.split('|')]
                if len(parts) >= 8 and parts[1] and parts[1] != '模块':
                    data["coverage"].append({
                        "module": parts[1],
                        "page_load": parts[2],
                        "search": parts[3],
                        "add": parts[4],
                        "edit": parts[5],
                        "delete": parts[6],
                        "coverage": parts[7]
                    })

        # 解析缺陷统计 - 使用更健壮的方式
        defect_section_start = md_content.find("## 四、缺陷统计")
        if defect_section_start != -1:
            next_section = md_content.find("\n## ", defect_section_start + 1)
            if next_section == -1:
                next_section = len(md_content)
            defect_section = md_content[defect_section_start:next_section]
            for line in defect_section.split('\n'):
                line = line.strip()
                if not line.startswith('|') or '严重' in line or '---' in line or '-:' in line:
                    continue
                parts = [p.strip() for p in line.split('|')]
                if len(parts) >= 3 and parts[1] and parts[1] != '严重程度':
                    severity = parts[1]
                    count = parts[2].replace('*', '').replace('**', '')
                    try:
                        count_num = int(count)
                    except:
                        count_num = 0
                    if '严重' in severity or 'Critical' in severity:
                        data["defects"]["critical"] = count_num
                    elif '高' in severity or 'High' in severity:
                        data["defects"]["high"] = count_num
                    elif '中' in severity or 'Medium' in severity:
                        data["defects"]["medium"] = count_num
                    elif '低' in severity or 'Low' in severity:
                        data["defects"]["low"] = count_num

        # 解析截图表 - 使用更健壮的方式，先找到整个章节
        screenshot_section_start = md_content.find("## 六、截图表")
        if screenshot_section_start != -1:
            # 找到下一个 ## 或文件结尾
            next_section = md_content.find("\n## ", screenshot_section_start + 1)
            if next_section == -1:
                next_section = len(md_content)
            screenshot_section = md_content[screenshot_section_start:next_section]
            for line in screenshot_section.split('\n'):
                line = line.strip()
                if not line.startswith('|') or '编号' in line or '---' in line or '-:' in line:
                    continue
                parts = [p.strip() for p in line.split('|')]
                if len(parts) >= 4 and parts[1] and parts[1] != '编号':
                    data["screenshots"].append({
                        "number": parts[1],
                        "filename": parts[2],
                        "description": parts[3]
                    })

        # 解析结论
        conclusion_match = re.search(
            r'## 七、结论\s*\n\n(.+?)(?=\n\n\*|$)',
            md_content, re.DOTALL
        )
        if conclusion_match:
            data["conclusion"] = conclusion_match.group(1).strip()

        return data

    def generate_html_report(self, data: dict, module_name: str = None, module_data: dict = None) -> str:
        """
        生成可视化 HTML 报告

        Args:
            data: 解析后的报告数据
            module_name: 模块名称（如果为单个模块生成报告）
            module_data: 单个模块的数据（如果为单个模块生成报告）

        Returns:
            HTML 字符串
        """
        # 使用整体数据或模块数据
        if module_data:
            passed = module_data["passed"]
            skipped = module_data["skipped"]
            failed = module_data["failed"]
            total = module_data["total"]
            steps = module_data["steps"]
            display_name = module_data["name"]
        else:
            passed = data["stats"]["passed"]
            skipped = data["stats"]["skipped"]
            failed = data["stats"]["failed"]
            total = data["stats"]["total"]
            steps = []
            display_name = "营销云用户菜单 - 回归测试"

        pass_rate = (passed / total * 100) if total > 0 else 0
        status_text = "✅ 全部通过" if failed == 0 else f"❌ {failed} 项失败"
        status_class = "pass" if failed == 0 else "fail"

        # 构建模块统计表格
        modules_table = ""
        if data["modules"]:
            rows = []
            for mod in data["modules"]:
                mod_pass_rate = (mod["passed"] / mod["total"] * 100) if mod["total"] > 0 else 0
                rate_color = "green" if mod_pass_rate >= 80 else "orange" if mod_pass_rate >= 50 else "red"
                rows.append(f"""
                <tr>
                    <td><strong>{mod['name']}</strong></td>
                    <td><span class="badge-green">{mod['passed']}</span></td>
                    <td><span class="badge-yellow">{mod['skipped']}</span></td>
                    <td><span class="badge-red">{mod['failed']}</span></td>
                    <td><span class="badge-{rate_color}">{mod_pass_rate:.0f}%</span></td>
                </tr>""")
            modules_table = f"""
            <table class="data-table">
                <thead>
                    <tr><th>模块</th><th>通过</th><th>跳过</th><th>失败</th><th>通过率</th></tr>
                </thead>
                <tbody>{''.join(rows)}</tbody>
            </table>"""

        # 构建覆盖率表格
        coverage_table = ""
        if data["coverage"]:
            rows = []
            for cov in data["coverage"]:
                cov_value = cov["coverage"].replace("**", "").replace("%", "")
                try:
                    cov_num = int(cov_value)
                except:
                    cov_num = 0
                cov_color = "green" if cov_num >= 60 else "orange" if cov_num >= 40 else "red"
                rows.append(f"""
                <tr>
                    <td><strong>{cov['module']}</strong></td>
                    <td>{cov['page_load']}</td>
                    <td>{cov['search']}</td>
                    <td>{cov['add']}</td>
                    <td>{cov['edit']}</td>
                    <td>{cov['delete']}</td>
                    <td><span class="badge-{cov_color}">{cov['coverage']}</span></td>
                </tr>""")
            coverage_table = f"""
            <table class="data-table">
                <thead>
                    <tr><th>模块</th><th>页面加载</th><th>搜索</th><th>新增</th><th>编辑</th><th>删除</th><th>覆盖率</th></tr>
                </thead>
                <tbody>{''.join(rows)}</tbody>
            </table>"""

        # 构建截图展示
        screenshots_html = ""
        if data["screenshots"]:
            items = []
            for i, ss in enumerate(data["screenshots"]):
                items.append(f"""
                <div class="screenshot-card">
                    <div class="screenshot-number">#{ss['number']}</div>
                    <div class="screenshot-desc">{ss['description']}</div>
                    <div class="screenshot-filename">{ss['filename']}</div>
                </div>""")
            screenshots_html = f"""
            <div class="screenshots-grid">
                {''.join(items)}
            </div>"""

        # 构建步骤明细（如果是单个模块）
        steps_html = ""
        if steps:
            items = []
            for step in steps:
                status_icon = "✅" if step["status"] == "pass" else "↪️" if step["status"] == "skip" else "❌"
                status_class = step["status"]
                items.append(f"""
                <div class="step-item {status_class}">
                    <div class="step-header">
                        <span class="step-num">步骤 {step['step']}</span>
                        <span class="step-status">{status_icon} {step['result']}</span>
                    </div>
                    <div class="step-operation">{step['operation']}</div>
                    <div class="step-note">{step['note']}</div>
                </div>""")
            steps_html = f"""
            <div class="steps-list">
                {''.join(items)}
            </div>"""

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{display_name} - 测试报告</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;
  background:#f5f7fa;
  color:#333;
  line-height:1.6
}}
.container{{
  max-width:1200px;
  margin:0 auto;
  padding:24px
}}

/* Header */
.header{{
  background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);
  color:#fff;
  padding:40px;
  border-radius:16px;
  margin-bottom:24px;
  display:flex;
  justify-content:space-between;
  align-items:center;
  box-shadow:0 4px 20px rgba(102,126,234,.3)
}}
.header h1{{
  font-size:28px;
  margin-bottom:8px;
  font-weight:700
}}
.header .subtitle{{
  opacity:.9;
  font-size:14px
}}
.header .badge{{
  padding:12px 28px;
  border-radius:24px;
  font-size:18px;
  font-weight:700;
  box-shadow:0 2px 12px rgba(0,0,0,.15)
}}
.badge.pass{{background:#52c41a;color:#fff}}
.badge.fail{{background:#ff4d4f;color:#fff}}
.badge.partial{{background:#faad14;color:#fff}}

/* Stats Cards */
.cards{{
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
  gap:16px;
  margin-bottom:24px
}}
.card{{
  background:#fff;
  border-radius:12px;
  padding:24px;
  box-shadow:0 2px 12px rgba(0,0,0,.06);
  transition:transform .2s,box-shadow .2s;
  text-align:center
}}
.card:hover{{
  transform:translateY(-2px);
  box-shadow:0 4px 20px rgba(0,0,0,.1)
}}
.card .label{{
  font-size:12px;
  color:#888;
  margin-bottom:8px;
  text-transform:uppercase;
  letter-spacing:.5px
}}
.card .value{{
  font-size:36px;
  font-weight:700
}}
.card .value.green{{color:#52c41a}}
.card .value.red{{color:#ff4d4f}}
.card .value.blue{{color:#1890ff}}
.card .value.orange{{color:#faad14}}
.card .value.purple{{color:#722ed1}}

/* Charts Row */
.charts-row{{
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:20px;
  margin-bottom:24px
}}
.chart-card{{
  background:#fff;
  border-radius:12px;
  padding:24px;
  box-shadow:0 2px 12px rgba(0,0,0,.06)
}}
.chart-card h3{{
  font-size:16px;
  margin-bottom:16px;
  color:#555;
  font-weight:600
}}
.chart-wrapper{{
  position:relative;
  height:280px
}}

/* Section */
.section{{
  background:#fff;
  border-radius:12px;
  padding:24px;
  margin-bottom:24px;
  box-shadow:0 2px 12px rgba(0,0,0,.06)
}}
.section-title{{
  font-size:18px;
  font-weight:700;
  color:#1a1a2e;
  margin-bottom:16px;
  display:flex;
  align-items:center;
  gap:8px
}}

/* Data Table */
.data-table{{
  width:100%;
  border-collapse:collapse;
  font-size:14px
}}
.data-table th{{
  background:#f8f9fa;
  padding:12px 16px;
  text-align:left;
  font-weight:600;
  color:#555;
  border-bottom:2px solid #e8e8e8
}}
.data-table td{{
  padding:12px 16px;
  border-bottom:1px solid #f0f0f0
}}
.data-table tr:hover td{{
  background:#fafafa
}}
.badge-green{{
  background:#f6ffed;
  color:#52c41a;
  padding:4px 12px;
  border-radius:10px;
  font-size:12px;
  font-weight:600
}}
.badge-red{{
  background:#fff1f0;
  color:#ff4d4f;
  padding:4px 12px;
  border-radius:10px;
  font-size:12px;
  font-weight:600
}}
.badge-yellow{{
  background:#fffbe6;
  color:#faad14;
  padding:4px 12px;
  border-radius:10px;
  font-size:12px;
  font-weight:600
}}
.badge-orange{{
  background:#fff7e6;
  color:#d46b08;
  padding:4px 12px;
  border-radius:10px;
  font-size:12px;
  font-weight:600
}}

/* Screenshots */
.screenshots-grid{{
  display:grid;
  grid-template-columns:repeat(auto-fill,minmax(280px,1fr));
  gap:16px
}}
.screenshot-card{{
  background:#f8f9fa;
  border-radius:12px;
  padding:16px;
  border:1px solid #e8e8e8;
  transition:transform .2s
}}
.screenshot-card:hover{{
  transform:scale(1.02);
  box-shadow:0 4px 12px rgba(0,0,0,.1)
}}
.screenshot-number{{
  font-size:24px;
  font-weight:700;
  color:#667eea;
  margin-bottom:8px
}}
.screenshot-desc{{
  font-size:14px;
  color:#333;
  font-weight:500;
  margin-bottom:4px
}}
.screenshot-filename{{
  font-size:12px;
  color:#888;
  font-family:monospace
}}

/* Steps */
.steps-list{{
  display:flex;
  flex-direction:column;
  gap:12px
}}
.step-item{{
  background:#f8f9fa;
  border-radius:10px;
  padding:16px;
  border-left:4px solid #d9d9d9
}}
.step-item.pass{{border-left-color:#52c41a;background:#f6ffed}}
.step-item.skip{{border-left-color:#faad14;background:#fffbe6}}
.step-item.fail{{border-left-color:#ff4d4f;background:#fff1f0}}
.step-header{{
  display:flex;
  justify-content:space-between;
  align-items:center;
  margin-bottom:8px
}}
.step-num{{
  font-weight:700;
  color:#333
}}
.step-status{{
  font-size:14px;
  font-weight:600
}}
.step-operation{{
  font-size:14px;
  color:#555;
  margin-bottom:4px
}}
.step-note{{
  font-size:12px;
  color:#888
}}

/* Info Grid */
.info-grid{{
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(250px,1fr));
  gap:16px
}}
.info-item{{
  display:flex;
  flex-direction:column;
  gap:4px
}}
.info-item .label{{
  font-size:12px;
  color:#888;
  text-transform:uppercase;
  letter-spacing:.5px
}}
.info-item .value{{
  font-size:14px;
  color:#333;
  font-weight:500
}}

/* Conclusion */
.conclusion-box{{
  background:linear-gradient(135deg,#f6ffed 0%,#e6f7ff 100%);
  border-radius:12px;
  padding:24px;
  border:1px solid #b7eb8f
}}
.conclusion-box .conclusion-title{{
  font-size:18px;
  font-weight:700;
  color:#52c41a;
  margin-bottom:12px
}}
.conclusion-box .conclusion-text{{
  font-size:14px;
  color:#333;
  line-height:2
}}

/* Footer */
.footer{{
  text-align:center;
  padding:24px;
  color:#aaa;
  font-size:13px
}}

/* Responsive */
@media (max-width:768px){{
  .header{{flex-direction:column;text-align:center;gap:16px}}
  .charts-row{{grid-template-columns:1fr}}
  .cards{{grid-template-columns:repeat(2,1fr)}}
}}
</style>
</head>
<body>
<div class="container">
  <!-- Header -->
  <div class="header">
    <div>
      <h1>🎯 {display_name}</h1>
      <div class="subtitle">Web 自动化测试报告 · 运行编号 {data['run_id']}</div>
      <div class="subtitle">{data['execution_time']}</div>
    </div>
    <div class="badge {status_class}">{status_text}</div>
  </div>

  <!-- Stats Cards -->
  <div class="cards">
    <div class="card">
      <div class="label">通过</div>
      <div class="value green">{passed}</div>
    </div>
    <div class="card">
      <div class="label">跳过</div>
      <div class="value orange">{skipped}</div>
    </div>
    <div class="card">
      <div class="label">失败</div>
      <div class="value red">{failed}</div>
    </div>
    <div class="card">
      <div class="label">合计</div>
      <div class="value blue">{total}</div>
    </div>
    <div class="card">
      <div class="label">通过率</div>
      <div class="value {'green' if pass_rate >= 80 else 'orange' if pass_rate >= 50 else 'red'}">{pass_rate:.1f}%</div>
    </div>
  </div>

  <!-- Charts -->
  <div class="charts-row">
    <div class="chart-card">
      <h3>📊 测试结果分布</h3>
      <div class="chart-wrapper">
        <canvas id="resultChart"></canvas>
      </div>
    </div>
    <div class="chart-card">
      <h3>📊 模块通过率对比</h3>
      <div class="chart-wrapper">
        <canvas id="moduleChart"></canvas>
      </div>
    </div>
  </div>

  <!-- Execution Info -->
  <div class="section">
    <div class="section-title">📋 执行信息</div>
    <div class="info-grid">
      <div class="info-item">
        <span class="label">运行编号</span>
        <span class="value">{data['run_id']}</span>
      </div>
      <div class="info-item">
        <span class="label">执行时间</span>
        <span class="value">{data['execution_time']}</span>
      </div>
      <div class="info-item">
        <span class="label">执行时长</span>
        <span class="value">{data['duration']}</span>
      </div>
      <div class="info-item">
        <span class="label">测试脚本</span>
        <span class="value">{data['script']}</span>
      </div>
      <div class="info-item">
        <span class="label">测试框架</span>
        <span class="value">{data['framework']}</span>
      </div>
      <div class="info-item">
        <span class="label">测试环境</span>
        <span class="value">{data['environment']}</span>
      </div>
      <div class="info-item">
        <span class="label">测试账号</span>
        <span class="value">{data['account']}</span>
      </div>
      <div class="info-item">
        <span class="label">整体结果</span>
        <span class="value">{data['overall_result']}</span>
      </div>
    </div>
  </div>

  <!-- Module Results -->
  <div class="section">
    <div class="section-title">📋 模块测试结果</div>
    {modules_table}
  </div>

  <!-- Coverage Analysis -->
  <div class="section">
    <div class="section-title">📊 测试覆盖分析</div>
    {coverage_table}
  </div>

  <!-- Defects -->
  <div class="section">
    <div class="section-title">🐛 缺陷统计</div>
    <div class="cards" style="margin-bottom:0">
      <div class="card">
        <div class="label">严重 (Critical)</div>
        <div class="value red">{data['defects']['critical']}</div>
      </div>
      <div class="card">
        <div class="label">高 (High)</div>
        <div class="value orange">{data['defects']['high']}</div>
      </div>
      <div class="card">
        <div class="label">中 (Medium)</div>
        <div class="value blue">{data['defects']['medium']}</div>
      </div>
      <div class="card">
        <div class="label">低 (Low)</div>
        <div class="value purple">{data['defects']['low']}</div>
      </div>
    </div>
  </div>

  <!-- Screenshots -->
  <div class="section">
    <div class="section-title">📸 截图列表 ({len(data['screenshots'])} 张)</div>
    {screenshots_html}
  </div>

  <!-- Steps Detail (if single module) -->
  {steps_html}

  <!-- Conclusion -->
  <div class="section">
    <div class="section-title">✅ 测试结论</div>
    <div class="conclusion-box">
      <div class="conclusion-title">测试结论</div>
      <div class="conclusion-text">{data['conclusion'].replace(chr(10), '<br>')}</div>
    </div>
  </div>

  <!-- Footer -->
  <div class="footer">
    <p>Web 自动化测试报告 · 生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
    <p>AI Test Agent System Platform</p>
  </div>
</div>

<script>
// Result Distribution Chart
const resultCtx = document.getElementById('resultChart').getContext('2d');
new Chart(resultCtx, {{
  type: 'doughnut',
  data: {{
    labels: ['通过', '跳过', '失败'],
    datasets: [{{
      data: [{passed}, {skipped}, {failed}],
      backgroundColor: ['#52c41a', '#faad14', '#ff4d4f'],
      borderWidth: 0,
      hoverOffset: 8
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    cutout: '60%',
    plugins: {{
      legend: {{
        position: 'bottom',
        labels: {{ padding: 20, font: {{ size: 14 }} }}
      }},
      tooltip: {{
        callbacks: {{
          label: function(context) {{
            const total = {total};
            const pct = total > 0 ? ((context.raw / total) * 100).toFixed(1) : 0;
            return context.label + ': ' + context.raw + ' (' + pct + '%)';
          }}
        }}
      }}
    }}
  }}
}});

// Module Pass Rate Chart
const moduleCtx = document.getElementById('moduleChart').getContext('2d');
const moduleLabels = {json.dumps([m['name'] for m in data['modules']])};
const moduleRates = {json.dumps([round(m['passed']/m['total']*100, 1) if m['total'] > 0 else 0 for m in data['modules']])};
new Chart(moduleCtx, {{
  type: 'bar',
  data: {{
    labels: moduleLabels,
    datasets: [{{
      label: '通过率 (%)',
      data: moduleRates,
      backgroundColor: moduleRates.map(r => r >= 80 ? '#52c41a' : r >= 50 ? '#faad14' : '#ff4d4f'),
      borderRadius: 8,
      borderSkipped: false
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          label: function(context) {{
            return '通过率: ' + context.raw + '%';
          }}
        }}
      }}
    }},
    scales: {{
      y: {{
        beginAtZero: true,
        max: 100,
        ticks: {{
          callback: function(value) {{ return value + '%'; }}
        }}
      }},
      x: {{
        ticks: {{
          font: {{ size: 12 }}
        }}
      }}
    }}
  }}
}});
</script>
</body>
</html>"""

        return html

    async def save_report_to_system(
        self,
        sub_function_id: str,
        html_content: str,
        report_name: str = "测试报告"
    ) -> dict:
        """
        保存 HTML 报告到 MinIO 和数据库

        Args:
            sub_function_id: 子功能 ID
            html_content: HTML 报告内容
            report_name: 报告名称

        Returns:
            dict: 包含 attachment_id 和 file_path 的字典
        """
        from sqlalchemy import select

        sub_function_uuid = UUID(sub_function_id)

        async with async_session_factory() as session:
            # 查询子功能
            sf_stmt = select(WebSubFunction).where(WebSubFunction.id == sub_function_uuid)
            sf_result = await session.execute(sf_stmt)
            sub_function = sf_result.scalar_one_or_none()

            if not sub_function:
                return {"error": f"Sub-function {sub_function_id} not found"}

            project_id = sub_function.project_id

            # 生成对象名称
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            object_name = f"web-tests/{self.project_identifier}/sub-functions/{sub_function_id}/test-report-{timestamp}.html"

            # 上传到 MinIO
            html_bytes = html_content.encode('utf-8')
            MinIOClient.upload_bytes(
                object_name=object_name,
                data=html_bytes,
                content_type="text/html"
            )

            # 创建附件记录
            safe_name = sub_function.display_name.replace(" ", "-").replace("/", "-").replace("\\", "-") if sub_function.display_name else "未命名功能"
            file_name = f"{safe_name}-{report_name}-{timestamp}.html"

            attachment = Attachment(
                entity_type=AttachmentEntityType.WEB_TEST_REPORT,
                entity_id=sub_function_uuid,
                project_id=project_id,
                file_name=file_name,
                file_size=len(html_bytes),
                content_type="text/html",
                object_name=object_name,
                description=f"Web 测试报告 - {sub_function.display_name} ({report_name})",
                created_by="web-agent"
            )
            session.add(attachment)
            await session.commit()
            await session.refresh(attachment)

            return {
                "success": True,
                "attachment_id": str(attachment.id),
                "file_name": file_name,
                "object_name": object_name,
                "sub_function_id": sub_function_id,
                "sub_function_name": sub_function.display_name
            }

    async def convert_and_save(
        self,
        md_content: str,
        sub_function_ids: dict = None
    ) -> list:
        """
        完整的转换和保存流程

        Args:
            md_content: Markdown 报告内容
            sub_function_ids: 模块名到子功能 ID 的映射，例如:
                {"会员管理": "uuid1", "标签管理": "uuid2", ...}

        Returns:
            list: 保存结果列表
        """
        # 1. 解析 Markdown
        data = self.parse_markdown_report(md_content)

        results = []

        # 2. 生成整体报告并保存
        overall_html = self.generate_html_report(data)

        # 3. 为每个模块生成单独报告
        for module in data["modules"]:
            module_name = module["name"]
            module_html = self.generate_html_report(data, module_name=module_name, module_data=module)

            # 查找对应的子功能 ID
            sf_id = None
            if sub_function_ids and module_name in sub_function_ids:
                sf_id = sub_function_ids[module_name]
            else:
                # 尝试从模块数据中的 WSF ID 查找
                # 或者从数据库中根据名称查找
                sf_id = await self._find_sub_function_id_by_name(module_name)

            if sf_id:
                result = await self.save_report_to_system(
                    sub_function_id=sf_id,
                    html_content=module_html,
                    report_name=f"run_{data['run_id']}"
                )
                results.append(result)
            else:
                results.append({
                    "error": f"未找到子功能 ID: {module_name}",
                    "module_name": module_name
                })

        return results

    async def _find_sub_function_id_by_name(self, name: str) -> str:
        """根据名称查找子功能 ID"""
        from sqlalchemy import select

        async with async_session_factory() as session:
            stmt = select(WebSubFunction).where(WebSubFunction.display_name == name)
            result = await session.execute(stmt)
            sub_function = result.scalar_one_or_none()
            if sub_function:
                return str(sub_function.id)
        return None


# 便捷函数
async def import_markdown_report(
    md_file_path: str,
    project_identifier: str = "PR-2",
    sub_function_ids: dict = None
) -> list:
    """
    导入 Markdown 测试报告到系统

    Args:
        md_file_path: Markdown 文件路径
        project_identifier: 项目标识符
        sub_function_ids: 模块名到子功能 ID 的映射

    Returns:
        list: 保存结果列表
    """
    # 读取 Markdown 文件
    md_path = Path(md_file_path)
    if not md_path.exists():
        return [{"error": f"文件不存在: {md_file_path}"}]

    md_content = md_path.read_text(encoding='utf-8')

    # 创建转换器
    converter = MarkdownReportConverter(project_identifier=project_identifier)

    # 转换并保存
    results = await converter.convert_and_save(md_content, sub_function_ids)

    return results
