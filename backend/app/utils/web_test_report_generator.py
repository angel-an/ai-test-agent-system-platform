"""
Web 测试 HTML 报告生成工具

提供生成美观的 HTML 测试报告功能，替代 Playwright 默认的 HTML 报告。
参考 mkt-test-report-20260625.html 的样式设计。

特性：
- 自包含 HTML（截图嵌入为 Base64，无需外部依赖）
- 支持在线查看和下载后本地打开
- 响应式设计，支持移动端
- 包含图表、统计卡片、测试场景、截图、日志
"""

import json
import base64
import os
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path


def generate_web_test_report_html(
    sub_function_name: str,
    sub_function_id: str,
    execution_result: Dict[str, Any],
    project_identifier: str = "",
    test_scenarios: Optional[List[Dict]] = None,
    screenshots: Optional[List[str]] = None,
    logs: Optional[str] = None,
    report_dir: Optional[str] = None,
) -> str:
    """
    生成美观的 Web 测试 HTML 报告（自包含版本）

    Args:
        sub_function_name: 子功能名称
        sub_function_id: 子功能 ID
        execution_result: 执行结果字典
        project_identifier: 项目标识符
        test_scenarios: 测试场景列表（可选）
        screenshots: 截图文件路径列表（可选）
        logs: 执行日志文本（可选）

    Returns:
        自包含 HTML 字符串（截图嵌入为 Base64）
    """
    # 提取执行结果数据
    duration = execution_result.get("duration", 0)
    return_code = execution_result.get("return_code", -1)
    stdout = execution_result.get("stdout", "")
    stderr = execution_result.get("stderr", "")
    success = execution_result.get("success", False)
    start_time = execution_result.get("start_time", "")
    end_time = execution_result.get("end_time", "")

    # 解析测试结果
    passed_count, failed_count, total_count, test_details = _parse_test_results(stdout, stderr, success, report_dir)

    # 判断是否有有意义的数据
    has_test_data = total_count > 0 or passed_count > 0 or failed_count > 0
    has_screenshots = screenshots is not None and len(screenshots) > 0

    # 计算通过率
    pass_rate = (passed_count / total_count * 100) if total_count > 0 else (100 if success else 0)

    # 状态判断
    if success:
        if has_test_data and pass_rate >= 100:
            status_text = "✅ 全部通过"
            status_class = "pass"
        elif has_test_data and pass_rate > 0:
            status_text = "⚠️ 部分通过"
            status_class = "partial"
        else:
            status_text = "✅ 执行成功"
            status_class = "pass"
    else:
        if has_test_data and failed_count > 0:
            status_text = f"❌ {failed_count} 项失败"
            status_class = "fail"
        else:
            status_text = "❌ 执行失败"
            status_class = "fail"

    # 格式化时间
    try:
        if start_time:
            start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            start_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    except:
        start_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 构建测试场景 HTML
    scenarios_html = ""
    if test_details:
        scenarios_html = _build_scenarios_html_from_details(test_details)
    elif test_scenarios:
        scenarios_html = _build_scenarios_html(test_scenarios)

    # 构建截图 HTML（嵌入 Base64）
    screenshots_html = ""
    all_screenshots = _collect_screenshots(screenshots, execution_result)
    if all_screenshots:
        screenshots_html = _build_screenshots_html_embedded(all_screenshots, report_dir)

    # 构建日志 HTML
    logs_html = ""
    if logs:
        logs_html = _build_logs_html(logs)

    # 构建 stdout 输出 HTML
    stdout_html = ""
    if stdout:
        stdout_html = _build_stdout_html(stdout, stderr, return_code, duration)

    # 如果没有测试数据，显示提示信息
    no_data_hint = ""
    if not has_test_data and not has_screenshots:
        no_data_hint = """
        <div class="no-data-hint">
          <div class="hint-icon">📋</div>
          <div class="hint-title">未检测到结构化测试数据</div>
          <div class="hint-desc">本次执行未生成 Playwright 标准格式的测试结果。可能原因：<br>
            1. 使用了自定义脚本格式（非标准 .spec.ts 文件）<br>
            2. 脚本执行过程中未调用测试断言<br>
            3. 执行被中断或超时<br>
            4. 截图功能未启用（可在脚本中使用 page.screenshot()）
          </div>
        </div>"""

    # 生成运行序号
    run_number = datetime.now().strftime("%Y%m%d%H%M%S")

    # 构建执行摘要信息
    exec_summary = ""
    if has_test_data:
        exec_summary = f"通过 {passed_count} 项 | 失败 {failed_count} 项 | 通过率 {pass_rate:.1f}%"
    else:
        exec_summary = "执行完成（无结构化测试数据）" if success else "执行失败"

    # 构建图表
    doughnut_chart = _build_svg_doughnut_chart(passed_count, failed_count)
    bar_chart = _build_svg_bar_chart(passed_count, failed_count)

    # 构建 HTML 文件
    parts = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="zh-CN">')
    parts.append("<head>")
    parts.append('<meta charset="UTF-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1.0">')
    parts.append(f"<title>{sub_function_name} - 测试报告 #{run_number}</title>")
    parts.append("<style>")
    parts.append(_get_css_styles())
    parts.append("</style>")
    parts.append("</head>")
    parts.append("<body>")
    parts.append('<div class="container">')

    # Header
    parts.append('  <div class="header">')
    parts.append('    <div>')
    parts.append(f'      <h1>🎯 {sub_function_name}</h1>')
    parts.append(f'      <div class="subtitle">Web 自动化测试报告 · 运行 #{run_number}</div>')
    parts.append(f'      <div class="subtitle">{exec_summary}</div>')
    parts.append('    </div>')
    parts.append(f'    <div class="badge {status_class}">{status_text}</div>')
    parts.append('  </div>')

    # Report Meta Bar
    parts.append('  <div class="report-meta">')
    parts.append(f'    <div class="meta-tag"><span class="meta-label">⏱ 执行时间</span><span class="meta-value">{start_str}</span></div>')
    parts.append(f'    <div class="meta-tag"><span class="meta-label">⏳ 耗时</span><span class="meta-value">{duration:.2f} 秒</span></div>')
    parts.append(f'    <div class="meta-tag"><span class="meta-label">📋 测试项</span><span class="meta-value">{total_count} 个</span></div>')
    parts.append(f'    <div class="meta-tag"><span class="meta-label">📁 项目</span><span class="meta-value">{project_identifier}</span></div>')
    parts.append(f'    <div class="meta-tag"><span class="meta-label">🔖 子功能</span><span class="meta-value" title="{sub_function_id}">{sub_function_name}</span></div>')
    parts.append('  </div>')

    # Stats Cards
    pass_rate_color = "green" if pass_rate >= 80 else "orange" if pass_rate >= 50 else "red"
    return_code_color = "green" if return_code == 0 else "red"
    parts.append('  <div class="cards">')
    parts.append(f'    <div class="card"><div class="label">执行时长</div><div class="value blue">{duration:.1f}s</div></div>')
    parts.append(f'    <div class="card"><div class="label">通过</div><div class="value green">{passed_count}</div></div>')
    parts.append(f'    <div class="card"><div class="label">失败</div><div class="value red">{failed_count}</div></div>')
    parts.append(f'    <div class="card"><div class="label">通过率</div><div class="value {pass_rate_color}">{pass_rate:.1f}%</div></div>')
    parts.append(f'    <div class="card"><div class="label">返回码</div><div class="value {return_code_color}">{return_code}</div></div>')
    parts.append(f'    <div class="card"><div class="label">截图数</div><div class="value purple">{len(all_screenshots) if all_screenshots else 0}</div></div>')
    parts.append('  </div>')

    # No data hint
    if no_data_hint:
        parts.append(no_data_hint)

    # Charts
    parts.append('  <div class="charts-row">')
    parts.append('    <div class="chart-card"><h3>📊 测试结果分布</h3><div class="chart-wrapper">')
    parts.append(doughnut_chart)
    parts.append('    </div></div>')
    parts.append('    <div class="chart-card"><h3>📊 执行状态</h3><div class="chart-wrapper">')
    parts.append(bar_chart)
    parts.append('    </div></div>')
    parts.append('  </div>')

    # Execution Info
    parts.append('  <div class="section-title">📋 执行信息</div>')
    parts.append('  <div class="info-panel">')
    parts.append('    <div class="info-grid">')
    parts.append(f'      <div class="info-item"><span class="label">开始时间</span><span class="value">{start_str}</span></div>')
    parts.append(f'      <div class="info-item"><span class="label">执行时长</span><span class="value">{duration:.2f} 秒</span></div>')
    success_str = "✅ 成功" if success else "❌ 失败"
    parts.append(f'      <div class="info-item"><span class="label">执行状态</span><span class="value">{success_str}</span></div>')
    parts.append(f'      <div class="info-item"><span class="label">返回码</span><span class="value">{return_code}</span></div>')
    parts.append('    </div>')
    parts.append('  </div>')

    # Scenarios, Screenshots, Stdout, Logs
    if scenarios_html:
        parts.append(scenarios_html)
    if screenshots_html:
        parts.append(screenshots_html)
    if stdout_html:
        parts.append(stdout_html)
    if logs_html:
        parts.append(logs_html)

    # Footer
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts.append('  <div class="footer">')
    parts.append(f'    <p>Web 自动化测试报告 · 生成时间: {now_str}</p>')
    parts.append('    <p>AI Test Agent System Platform</p>')
    parts.append('  </div>')

    parts.append('</div>')

    # JavaScript
    parts.append("<script>")
    parts.append("""
function toggleCollapse(btn) {
  btn.classList.toggle('active');
  var content = btn.nextElementSibling;
  content.classList.toggle('open');
  var icon = btn.querySelector('.toggle-icon');
  icon.textContent = content.classList.contains('open') ? '▼' : '▶';
}
document.addEventListener('DOMContentLoaded', function() {
  var firstBtn = document.querySelector('.collapsible-header');
  if (firstBtn) toggleCollapse(firstBtn);
});

// 截图点击放大功能
function openScreenshotModal(src, caption) {
  var modal = document.getElementById('screenshot-modal');
  var img = document.getElementById('modal-img');
  var cap = document.getElementById('modal-caption');
  img.src = src;
  cap.textContent = caption || '';
  modal.style.display = 'flex';
  document.body.style.overflow = 'hidden';
}
function closeScreenshotModal() {
  var modal = document.getElementById('screenshot-modal');
  modal.style.display = 'none';
  document.body.style.overflow = '';
}
// 点击模态框背景关闭
document.addEventListener('DOMContentLoaded', function() {
  var modal = document.getElementById('screenshot-modal');
  if (modal) {
    modal.addEventListener('click', function(e) {
      if (e.target === modal) closeScreenshotModal();
    });
    // ESC 键关闭
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') closeScreenshotModal();
    });
  }
});
""")
    parts.append("</script>")

    # 截图放大模态框(放在 body 末尾)
    parts.append("""
<!-- 截图放大模态框 -->
<div id="screenshot-modal" class="screenshot-modal" style="display:none;">
  <div class="screenshot-modal-content">
    <span class="screenshot-modal-close" onclick="closeScreenshotModal()">&times;</span>
    <img id="modal-img" src="" alt="放大截图" />
    <div id="modal-caption" class="screenshot-modal-caption"></div>
  </div>
</div>
""")
    parts.append("</body>")
    parts.append("</html>")

    return "\n".join(parts)


def _get_css_styles() -> str:
    """返回 CSS 样式字符串"""
    return """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;background:#f0f2f5;color:#333;line-height:1.6}
.container{max-width:1200px;margin:0 auto;padding:20px}
.header{background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);color:#fff;padding:40px;border-radius:16px;margin-bottom:24px;display:flex;justify-content:space-between;align-items:center;box-shadow:0 4px 20px rgba(0,0,0,.15)}
.header h1{font-size:28px;margin-bottom:8px;font-weight:700}
.header .subtitle{opacity:.8;font-size:14px;margin-bottom:4px}
.header .badge{padding:10px 28px;border-radius:20px;font-size:18px;font-weight:700;box-shadow:0 2px 8px rgba(0,0,0,.2)}
.badge.pass{background:#52c41a;color:#fff}
.badge.fail{background:#ff4d4f;color:#fff}
.badge.partial{background:#faad14;color:#fff}
.report-meta{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:24px}
.meta-tag{background:#fff;border-radius:8px;padding:8px 16px;font-size:13px;color:#555;box-shadow:0 1px 4px rgba(0,0,0,.06);display:flex;align-items:center;gap:6px}
.meta-tag .meta-label{color:#888;font-size:12px}
.meta-tag .meta-value{font-weight:600;color:#333}
.no-data-hint{background:linear-gradient(135deg,#fff7e6 0%,#fffbe6 100%);border:1px solid #ffd591;border-radius:12px;padding:32px;margin:24px 0;text-align:center}
.no-data-hint .hint-icon{font-size:48px;margin-bottom:12px}
.no-data-hint .hint-title{font-size:18px;font-weight:600;color:#ad6800;margin-bottom:8px}
.no-data-hint .hint-desc{font-size:14px;color:#8c6d1f;line-height:2}
.output-summary{background:#f6ffed;border:1px solid #b7eb8f;border-radius:8px;padding:16px;margin-bottom:16px;display:flex;gap:24px;flex-wrap:wrap}
.output-summary .summary-item{display:flex;align-items:center;gap:8px}
.output-summary .summary-item .label{font-size:12px;color:#666;text-transform:uppercase;letter-spacing:.5px}
.output-summary .summary-item .value{font-size:16px;font-weight:700;color:#333}
.output-summary .summary-item .value.success{color:#52c41a}
.output-summary .summary-item .value.error{color:#ff4d4f}
.collapsible{margin-bottom:12px}
.collapsible-header{background:#f0f2f5;border:none;border-radius:8px;padding:12px 16px;width:100%;text-align:left;font-size:14px;font-weight:600;color:#333;cursor:pointer;display:flex;justify-content:space-between;align-items:center}
.collapsible-header:hover{background:#e8e8e8}
.collapsible-header .toggle-icon{font-size:12px;transition:transform .2s}
.collapsible-header.active .toggle-icon{transform:rotate(90deg)}
.collapsible-content{max-height:0;overflow:hidden;transition:max-height .3s ease-out}
.collapsible-content.open{max-height:2000px;overflow:auto}
.ansi-green{color:#52c41a;font-weight:600}
.ansi-red{color:#ff4d4f;font-weight:600}
.ansi-yellow{color:#faad14}
.ansi-blue{color:#1890ff}
.ansi-gray{color:#999}
.test-step{padding:8px 12px;border-left:3px solid #d9d9d9;margin:4px 0;font-size:13px}
.test-step.pass{border-left-color:#52c41a;background:#f6ffed}
.test-step.fail{border-left-color:#ff4d4f;background:#fff1f0}
.test-step .step-title{font-weight:600;margin-bottom:2px}
.test-step .step-detail{color:#666;font-size:12px}
.output-section{background:#fff;border-radius:8px;padding:12px 16px;margin:8px 0;border-left:3px solid #1890ff}
.output-section.page-info{border-left-color:#1890ff;background:#e6f7ff}
.output-section.elements{border-left-color:#52c41a;background:#f6ffed}
.output-section.locators{border-left-color:#722ed1;background:#f9f0ff}
.output-section.assertions{border-left-color:#faad14;background:#fffbe6}
.output-section .section-title-small{font-size:13px;font-weight:600;margin-bottom:8px;color:#333}
.output-section .info-row,.output-section .element-row,.output-section .locator-row,.output-section .assert-row{font-size:12px;line-height:1.8;padding:2px 0}
.output-section .attr-name{color:#1890ff;font-weight:600}
.output-section .attr-value{color:#52c41a}
.screenshot-mark{background:#f6ffed;border:1px dashed #52c41a;border-radius:8px;padding:8px 12px;margin:4px 0;font-size:12px;color:#52c41a}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:24px}
.card{background:#fff;border-radius:12px;padding:24px;box-shadow:0 2px 8px rgba(0,0,0,.06);transition:transform .2s,box-shadow .2s}
.card:hover{transform:translateY(-2px);box-shadow:0 4px 16px rgba(0,0,0,.1)}
.card .label{font-size:12px;color:#888;margin-bottom:8px;text-transform:uppercase;letter-spacing:.5px}
.card .value{font-size:32px;font-weight:700}
.card .value.green{color:#52c41a}
.card .value.red{color:#ff4d4f}
.card .value.blue{color:#1890ff}
.card .value.orange{color:#faad14}
.card .value.purple{color:#722ed1}
.section-title{font-size:18px;margin:32px 0 16px;color:#1a1a2e;display:flex;align-items:center;gap:8px;font-weight:600}
.info-panel{background:#fff;border-radius:12px;padding:24px;box-shadow:0 2px 8px rgba(0,0,0,.06);margin-bottom:24px}
.info-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:16px}
.info-item{display:flex;flex-direction:column;gap:4px}
.info-item .label{font-size:12px;color:#888;text-transform:uppercase;letter-spacing:.5px}
.info-item .value{font-size:14px;color:#333;font-weight:500}
.scenario-grid{display:grid;gap:12px;margin-bottom:24px}
.scenario-card{background:#fff;border-radius:12px;padding:20px;box-shadow:0 2px 8px rgba(0,0,0,.06);border-left:4px solid #52c41a;transition:transform .2s}
.scenario-card:hover{transform:translateX(4px)}
.scenario-card.fail{border-left-color:#ff4d4f}
.scenario-card.partial{border-left-color:#faad14}
.scenario-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.scenario-name{font-size:16px;font-weight:600;color:#1a1a2e}
.status-tag{padding:4px 12px;border-radius:10px;font-size:12px;font-weight:600}
.status-tag.pass{background:#f6ffed;color:#52c41a}
.status-tag.fail{background:#fff1f0;color:#ff4d4f}
.status-tag.partial{background:#fff7e6;color:#faad14}
.scenario-detail{font-size:13px;color:#666;line-height:1.8}
.scenario-detail strong{color:#333}
.scenario-error{margin-top:12px;padding:12px;background:#fff1f0;border:1px solid #ffccc7;border-radius:8px}
.scenario-error .error-title{font-size:12px;font-weight:600;color:#ff4d4f;margin-bottom:8px}
.scenario-error .error-content{font-family:'Fira Code','Consolas',monospace;font-size:11px;color:#ff4d4f;line-height:1.6;white-space:pre-wrap;word-break:break-word;max-height:200px;overflow-y:auto}
.screenshot-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;margin-bottom:24px}
.screenshot-item{background:#fff;border-radius:12px;padding:12px;box-shadow:0 2px 8px rgba(0,0,0,.06);transition:transform .2s}
.screenshot-item:hover{transform:scale(1.02);cursor:pointer}
.screenshot-item img{width:100%;border-radius:8px;border:1px solid #e8e8e8}
.screenshot-item .caption{font-size:12px;color:#666;margin-top:8px;text-align:center}
/* 截图放大模态框样式 */
.screenshot-modal{position:fixed;z-index:9999;left:0;top:0;width:100%;height:100%;background-color:rgba(0,0,0,0.85);display:flex;align-items:center;justify-content:center;padding:20px;box-sizing:border-box}
.screenshot-modal-content{position:relative;max-width:90%;max-height:90%;display:flex;flex-direction:column;align-items:center}
.screenshot-modal-content img{max-width:100%;max-height:calc(90vh - 60px);border-radius:8px;box-shadow:0 4px 20px rgba(0,0,0,0.3)}
.screenshot-modal-close{position:absolute;top:-40px;right:0;color:#fff;font-size:32px;font-weight:bold;cursor:pointer;line-height:1;padding:0 8px}
.screenshot-modal-close:hover{color:#ff4d4f}
.screenshot-modal-caption{color:#fff;margin-top:12px;font-size:14px;text-align:center;max-width:100%;word-break:break-word}
.logs-panel{background:#1a1a2e;border-radius:12px;padding:20px;margin-bottom:24px;overflow-x:auto}
.logs-panel pre{color:#a6accd;font-family:'Fira Code','Consolas',monospace;font-size:13px;line-height:1.8;white-space:pre-wrap;word-break:break-word}
.logs-panel .log-time{color:#5c6370}
.logs-panel .log-info{color:#82aaff}
.logs-panel .log-error{color:#ff5370}
.logs-panel .log-warn{color:#ffcb6b}
.logs-panel .log-success{color:#c3e88d}
.stdout-panel{background:#fff;border-radius:12px;padding:20px;margin-bottom:24px;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.stdout-panel pre{font-family:'Fira Code','Consolas',monospace;font-size:13px;line-height:1.8;white-space:pre-wrap;word-break:break-word;color:#333;background:#fafafa;padding:16px;border-radius:8px;overflow-x:auto}
.footer{text-align:center;padding:24px;color:#aaa;font-size:13px}
.charts-row{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}
.chart-card{background:#fff;border-radius:12px;padding:24px;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.chart-card h3{font-size:16px;margin-bottom:16px;color:#555;font-weight:600}
.chart-wrapper{position:relative;height:280px}
.svg-chart{width:100%;height:100%}
@media (max-width:768px){
.header{flex-direction:column;text-align:center;gap:16px}
.charts-row{grid-template-columns:1fr}
.cards{grid-template-columns:repeat(2,1fr)}
}
"""


def _build_svg_doughnut_chart(passed: int, failed: int) -> str:
    """构建内联 SVG 环形图（无需外部依赖）"""
    import math

    total = passed + failed
    if total == 0:
        return '<div style="text-align:center;padding:40px;color:#999">暂无数据</div>'

    passed_pct = (passed / total) * 100
    failed_pct = (failed / total) * 100

    # 计算角度
    passed_angle = (passed / total) * 360

    # SVG 参数 - 增加高度以容纳图例，避免重叠
    chart_size = 200  # 图表区域大小
    legend_height = 50  # 图例区域高度
    total_height = chart_size + legend_height  # 总高度
    cx = chart_size / 2
    cy = chart_size / 2 - 10  # 稍微上移中心，给图例留空间
    r = 70  # 减小半径，避免图例与图表重叠
    stroke_width = 25  # 减小描边宽度

    # 计算圆弧路径
    def arc_path(start_angle, end_angle):
        start_rad = math.radians(start_angle - 90)
        end_rad = math.radians(end_angle - 90)
        x1 = cx + r * math.cos(start_rad)
        y1 = cy + r * math.sin(start_rad)
        x2 = cx + r * math.cos(end_rad)
        y2 = cy + r * math.sin(end_rad)
        large_arc = 1 if (end_angle - start_angle) > 180 else 0
        return f"M {x1} {y1} A {r} {r} 0 {large_arc} 1 {x2} {y2}"

    # 通过路径
    passed_path = arc_path(0, passed_angle)
    # 失败路径
    failed_path = arc_path(passed_angle, 360)

    return f"""
<svg class="svg-chart" viewBox="0 0 {chart_size} {total_height}" xmlns="http://www.w3.org/2000/svg">
  <g transform="translate(0, 0)">
    <path d="{passed_path}" fill="none" stroke="#52c41a" stroke-width="{stroke_width}" stroke-linecap="round"/>
    <path d="{failed_path}" fill="none" stroke="#ff4d4f" stroke-width="{stroke_width}" stroke-linecap="round"/>
    <text x="{cx}" y="{cy - 5}" text-anchor="middle" font-size="28" font-weight="bold" fill="#333">{total}</text>
    <text x="{cx}" y="{cy + 18}" text-anchor="middle" font-size="12" fill="#666">测试总数</text>
  </g>
  <!-- 图例区域 - 放在图表下方，不重叠 -->
  <g transform="translate(0, {chart_size + 5})">
    <rect x="{cx - 70}" y="0" width="12" height="12" fill="#52c41a" rx="2"/>
    <text x="{cx - 54}" y="10" font-size="11" fill="#666">通过 {passed} ({passed_pct:.1f}%)</text>
    <rect x="{cx + 10}" y="0" width="12" height="12" fill="#ff4d4f" rx="2"/>
    <text x="{cx + 26}" y="10" font-size="11" fill="#666">失败 {failed} ({failed_pct:.1f}%)</text>
  </g>
</svg>"""


def _build_svg_bar_chart(passed: int, failed: int) -> str:
    """构建内联 SVG 柱状图（无需外部依赖）"""
    max_val = max(passed, failed, 1)

    # SVG 参数
    width = 280
    height = 200
    bar_width = 60
    max_bar_height = 120

    # 计算柱子高度
    passed_height = (passed / max_val) * max_bar_height
    failed_height = (failed / max_val) * max_bar_height

    # 柱子位置
    passed_x = 80
    failed_x = 180
    base_y = 160

    return f"""
<svg class="svg-chart" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
  <rect x="{passed_x}" y="{base_y - passed_height}" width="{bar_width}" height="{passed_height}" fill="#52c41a" rx="4"/>
  <text x="{passed_x + bar_width/2}" y="{base_y - passed_height - 8}" text-anchor="middle" font-size="14" font-weight="bold" fill="#52c41a">{passed}</text>
  <text x="{passed_x + bar_width/2}" y="{base_y + 20}" text-anchor="middle" font-size="12" fill="#666">通过</text>

  <rect x="{failed_x}" y="{base_y - failed_height}" width="{bar_width}" height="{failed_height}" fill="#ff4d4f" rx="4"/>
  <text x="{failed_x + bar_width/2}" y="{base_y - failed_height - 8}" text-anchor="middle" font-size="14" font-weight="bold" fill="#ff4d4f">{failed}</text>
  <text x="{failed_x + bar_width/2}" y="{base_y + 20}" text-anchor="middle" font-size="12" fill="#666">失败</text>

  <line x1="30" y1="{base_y}" x2="{width - 30}" y2="{base_y}" stroke="#e8e8e8" stroke-width="1"/>
</svg>"""


def _build_scenarios_html(scenarios: List[Dict]) -> str:
    """构建测试场景 HTML"""
    items = []
    for scenario in scenarios:
        status = scenario.get("status", "pass")
        status_class = status
        status_text = {"pass": "✅ 通过", "fail": "❌ 失败", "partial": "⚠️ 部分通过"}.get(status, "✅ 通过")
        name = scenario.get("name", "未命名场景")
        detail = scenario.get("detail", "")

        items.append(f"""
    <div class="scenario-card {status_class}">
      <div class="scenario-header">
        <span class="scenario-name">{name}</span>
        <span class="status-tag {status_class}">{status_text}</span>
      </div>
      <div class="scenario-detail">{detail}</div>
    </div>""")

    return f"""
  <!-- Test Scenarios -->
  <div class="section-title">📋 测试场景</div>
  <div class="scenario-grid">
    {''.join(items)}
  </div>"""


def _build_screenshots_html_embedded(screenshots: List[str], report_dir: Optional[str] = None) -> str:
    """构建截图展示 HTML（截图嵌入为 Base64）

    Args:
        screenshots: 截图路径列表（相对路径或绝对路径）
        report_dir: 报告目录路径，用于解析相对路径
    """
    items = []
    for i, path in enumerate(screenshots):
        name = Path(path).name
        img_src = None
        img_bytes = None

        # 尝试读取图片并转换为 Base64
        # 策略1: 直接作为绝对路径尝试
        try_paths = []
        if Path(path).is_absolute():
            try_paths.append(Path(path))
        else:
            # 相对路径：尝试多个可能的基目录
            # 1. 如果提供了 report_dir，优先使用
            if report_dir:
                try_paths.append(Path(report_dir) / path)
                # 也尝试 report_dir 的父目录（因为截图可能在项目根目录）
                try_paths.append(Path(report_dir).parent / path)

            # 2. 尝试当前工作目录
            try_paths.append(Path.cwd() / path)

            # 3. 尝试从环境变量获取 workspace 根目录
            workspace_roots = [
                os.environ.get("WEB_CLI_WORKSPACE_ROOT", ""),
                os.environ.get("WEB_MCP_WORKSPACE_ROOT", ""),
                os.environ.get("WEBWRIGHT_WORKSPACE_ROOT", ""),
            ]
            for root in workspace_roots:
                if root:
                    try_paths.append(Path(root) / path)
                    try_paths.append(Path(root) / "tests" / path)

        # 尝试所有可能的路径
        for try_path in try_paths:
            try:
                if try_path.exists():
                    img_bytes = try_path.read_bytes()
                    img_base64 = base64.b64encode(img_bytes).decode("utf-8")
                    img_src = f"data:image/png;base64,{img_base64}"
                    print(f"[Web Report] 截图嵌入成功: {try_path}")
                    break
            except Exception:
                continue

        # 如果所有路径都失败，记录错误并使用占位符
        if img_src is None:
            print(f"[Web Report] 截图嵌入失败，无法找到文件: {path} (尝试路径: {[str(p) for p in try_paths]})")
            img_src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='400' height='300'%3E%3Crect fill='%23f5f5f5' width='400' height='300'/%3E%3Ctext x='50%25' y='50%25' text-anchor='middle' dy='.3em' fill='%23999' font-family='sans-serif' font-size='14'%3E截图加载失败%3C/text%3E%3C/svg%3E"

        # 使用 onclick 调用放大功能
        items.append(
            f"""
    <div class="screenshot-item" onclick="openScreenshotModal('{img_src}', '{name}')">
      <img src="{img_src}" alt="截图 {i+1}" loading="lazy" />
      <div class="caption">{name}</div>
    </div>"""
        )

    return f"""
  <!-- Screenshots -->
  <div class="section-title">📸 测试截图 ({len(screenshots)} 张)</div>
  <div class="screenshot-grid">
    {''.join(items)}
  </div>"""


def _build_logs_html(logs: str) -> str:
    """构建日志展示 HTML"""
    # 简单的日志着色
    colored_logs = logs
    return f"""
  <!-- Execution Logs -->
  <div class="section-title">📝 执行日志</div>
  <div class="logs-panel">
    <pre>{colored_logs}</pre>
  </div>"""


def _build_stdout_html(stdout: str, stderr: str = "", return_code: int = 0, duration: float = 0) -> str:
    """构建增强的 stdout 输出 HTML，支持折叠和格式化"""
    # 解析并格式化输出
    formatted_stdout = _format_console_output(stdout)
    formatted_stderr = _format_console_output(stderr) if stderr else ""

    # 生成执行摘要
    summary_items = []
    if return_code is not None:
        summary_items.append(f'<div class="summary-item"><span class="label">返回码</span><span class="value {"success" if return_code == 0 else "error"}">{return_code}</span></div>')
    if duration:
        summary_items.append(f'<div class="summary-item"><span class="label">耗时</span><span class="value">{duration:.2f}s</span></div>')

    # 统计信息
    lines_count = len(stdout.splitlines()) if stdout else 0
    summary_items.append(f'<div class="summary-item"><span class="label">输出行数</span><span class="value">{lines_count}</span></div>')

    summary_html = ""
    if summary_items:
        summary_html = f'<div class="output-summary">{ "".join(summary_items) }</div>'

    # 构建可折叠的输出区域
    sections = []

    # 标准输出
    if formatted_stdout:
        sections.append("""
    <div class="collapsible">
      <button class="collapsible-header" onclick="toggleCollapse(this)">
        <span>📤 标准输出 (""" + str(lines_count) + """ 行)</span>
        <span class="toggle-icon">▶</span>
      </button>
      <div class="collapsible-content">
        <div class="stdout-panel">
          <pre>""" + formatted_stdout + """</pre>
        </div>
      </div>
    </div>""")

    # 错误输出
    if formatted_stderr:
        stderr_lines = len(stderr.splitlines())
        sections.append("""
    <div class="collapsible">
      <button class="collapsible-header" onclick="toggleCollapse(this)" style="background:#fff1f0;border-color:#ff4d4f">
        <span>⚠️ 错误输出 (""" + str(stderr_lines) + """ 行)</span>
        <span class="toggle-icon">▶</span>
      </button>
      <div class="collapsible-content">
        <div class="stdout-panel" style="background:#fff1f0">
          <pre style="background:#fff1f0;color:#ff4d4f">""" + formatted_stderr + """</pre>
        </div>
      </div>
    </div>""")

    return """
  <!-- Execution Output -->
  <div class="section-title">📤 执行输出</div>
  """ + summary_html + """
  <div style="margin-bottom:24px">
    """ + "".join(sections) + """
  </div>"""


def _format_console_output(text: str) -> str:
    """格式化控制台输出，添加简单的语法高亮和结构化解析"""
    if not text:
        return ""

    import html
    import re

    lines = text.splitlines()
    formatted = []
    in_section = None  # 当前所在的区块类型
    section_buffer = []  # 区块缓冲

    def flush_section():
        """刷新当前区块到 formatted"""
        nonlocal in_section, section_buffer
        if not section_buffer:
            return

        if in_section == "page_info":
            # 页面信息区块
            formatted.append('<div class="output-section page-info">')
            formatted.append('  <div class="section-title-small">📍 页面信息</div>')
            for item in section_buffer:
                formatted.append(f'  <div class="info-row">{item}</div>')
            formatted.append('</div>')
        elif in_section == "elements":
            # 元素探测区块
            formatted.append('<div class="output-section elements">')
            formatted.append('  <div class="section-title-small">🔍 页面元素</div>')
            for item in section_buffer:
                formatted.append(f'  <div class="element-row">{item}</div>')
            formatted.append('</div>')
        elif in_section == "locators":
            # 定位器区块
            formatted.append('<div class="output-section locators">')
            formatted.append('  <div class="section-title-small">🎯 关键定位器</div>')
            for item in section_buffer:
                formatted.append(f'  <div class="locator-row">{item}</div>')
            formatted.append('</div>')
        elif in_section == "assertions":
            # 断言区块
            formatted.append('<div class="output-section assertions">')
            formatted.append('  <div class="section-title-small">✅ 断言验证</div>')
            for item in section_buffer:
                formatted.append(f'  <div class="assert-row">{item}</div>')
            formatted.append('</div>')
        else:
            # 普通输出
            for item in section_buffer:
                formatted.append(item)

        section_buffer = []
        in_section = None

    for line in lines:
        escaped = html.escape(line)
        stripped = line.strip()

        # 检测区块开始
        if re.match(r'^={3,}\s*(页面标题|当前URL|页面结构|页面信息)', stripped):
            flush_section()
            in_section = "page_info"
            continue
        elif re.match(r'^找到\s+\d+\s+(个|条)', stripped) or re.match(r'^(Input|Button|Link|Label):', stripped):
            if in_section != "elements":
                flush_section()
                in_section = "elements"
            # 元素行高亮
            escaped = _highlight_element_line(escaped)
            section_buffer.append(escaped)
            continue
        elif re.match(r'^={3,}\s*(关键定位器|定位器信息)', stripped):
            flush_section()
            in_section = "locators"
            continue
        elif re.match(r'^={3,}\s*(断言|验证|Verify)', stripped) or re.match(r'^Verify:', stripped):
            flush_section()
            in_section = "assertions"
            continue
        elif re.match(r'^\[SCREENSHOT', stripped):
            # 截图标记行
            flush_section()
            formatted.append(f'<div class="screenshot-mark">📸 {escaped}</div>')
            continue

        # 普通行着色
        if any(kw in line for kw in ['✓', 'passed', 'PASS', '成功', 'OK', '通过']):
            escaped = f'<span class="ansi-green">{escaped}</span>'
        elif any(kw in line for kw in ['✘', 'failed', 'FAIL', '失败', 'Error', 'error', '超时']):
            escaped = f'<span class="ansi-red">{escaped}</span>'
        elif any(kw in line for kw in ['WARN', 'warning', '警告', '⚠️']):
            escaped = f'<span class="ansi-yellow">{escaped}</span>'
        elif any(kw in line for kw in ['INFO', 'info', '→', '→']):
            escaped = f'<span class="ansi-blue">{escaped}</span>'
        elif line.startswith('  ') or line.startswith('    '):
            escaped = f'<span class="ansi-gray">{escaped}</span>'

        if in_section:
            section_buffer.append(escaped)
        else:
            formatted.append(escaped)

    flush_section()
    return '\n'.join(formatted)


def _highlight_element_line(line: str) -> str:
    """高亮元素描述行"""
    import re
    # 高亮属性名
    line = re.sub(r'(\w+)=', r'<span class="attr-name">\1</span>=', line)
    # 高亮属性值
    line = re.sub(r'="([^"]*)"', r'="<span class="attr-value">\1</span>"', line)
    # 高亮数量
    line = re.sub(r'(找到\s+)(\d+)', r'\1<span class="ansi-blue">\2</span>', line)
    return line


def _parse_test_results(stdout: str, stderr: str, success: bool, report_dir: Optional[str] = None) -> tuple:
    """解析测试结果，返回 (通过数, 失败数, 总数, 测试详情列表)

    支持多种测试结果格式：
    1. Playwright 标准输出格式
    2. self_reflect_result.json（webwright 脚本生成的结构化结果）
    3. stdout 中的通过/失败标记行
    """
    passed_count = 0
    failed_count = 0
    total_count = 0
    test_details = []

    # 首先尝试从 self_reflect_result.json 解析（webwright 模式）
    if report_dir:
        sr_results = _parse_self_reflect_json(report_dir)
        if sr_results:
            return sr_results

    if not stdout:
        return passed_count, failed_count, total_count, test_details

    import re

    # 方法1: 解析 Playwright 标准输出格式（总结行）
    # 例如: "  5 passed (2.3s)" 或 "  1 failed, 4 passed"
    summary_passed = 0
    summary_failed = 0
    for line in stdout.splitlines():
        line = line.strip()

        # 匹配 "N passed (Xs)" 格式 - 必须是总结行（行首数字）
        m = re.search(r'^(\d+)\s+passed(?:\s*\([^)]*\))?$', line, re.IGNORECASE)
        if m:
            summary_passed = int(m.group(1))

        # 匹配 "N failed" 格式 - 必须是总结行（行首数字）
        m = re.search(r'^(\d+)\s+failed', line, re.IGNORECASE)
        if m:
            summary_failed = int(m.group(1))

        # 匹配 "N skipped" 格式
        m = re.search(r'^(\d+)\s+skipped', line, re.IGNORECASE)
        if m:
            pass  # skipped 不计入通过或失败

    # 方法2: 解析 Playwright 详细测试输出格式（逐行解析）
    # 格式: "  x  1 [chromium] › file.spec.ts:258:7 › test name (4.2s)"
    # 格式: "  ✓  1 [chromium] › file.spec.ts:258:7 › test name (4.2s)"
    # 格式: "  -  1 [chromium] › file.spec.ts:258:7 › test name (4.2s)" (skipped)
    lines = stdout.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # 匹配 Playwright 测试行格式
        # 支持: "  x  N [browser] › file › test name (duration)" 或 "  ✓  N [browser] › file › test name"
        # 也支持: "  N [browser] › file › test name" (无状态标记)
        # 注意: Playwright 使用 › (U+203A) 字符，但某些环境可能显示为 >
        pw_match = re.match(r'^[\s]*([✓✔√✘✗×\-x])?[\s]*(\d+)?\s*\[(\w+)\]\s*[›>]\s*([^›>]+)[›>]\s*(.+)$', line)
        if not pw_match:
            # 后备: 尝试使用 ASCII > 字符匹配
            pw_match = re.match(r'^[\s]*([✓✔√✘✗×\-x])?[\s]*(\d+)?\s*\[(\w+)\]\s*>\s*([^>]+)>\s*(.+)$', line)
        if pw_match:
            status_marker = pw_match.group(1) or ''
            browser = pw_match.group(3)
            file_info = pw_match.group(4).strip()
            test_name = pw_match.group(5).strip()

            # 从测试名中移除持续时间
            test_name = re.sub(r'\s*\(\d+\.?\d*[ms]\)\s*$', '', test_name)

            # 判断状态
            if status_marker in ['✓', '✔', '√']:
                status = "pass"
                passed_count += 1
            elif status_marker in ['✘', '✗', '×', 'x']:
                status = "fail"
                failed_count += 1
            elif status_marker == '-':
                status = "skip"
            else:
                # 没有明确标记，检查下一行是否有错误信息
                status = "pass"  # 默认通过
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    # 如果下一行是错误信息（缩进或包含 Error:），则标记为失败
                    if next_line.startswith('Error:') or 'Error:' in next_line[:50]:
                        status = "fail"
                        failed_count += 1
                    elif re.match(r'^[\s]+\d+\)', next_line):  # 错误堆栈行
                        status = "fail"
                        failed_count += 1

            # 收集错误详情
            error_detail = ""
            if status == "fail":
                # 收集后续的错误信息行
                j = i + 1
                while j < len(lines) and j < i + 30:  # 最多收集30行
                    next_line = lines[j]
                    # 如果遇到新的测试行或空行，停止收集
                    # 支持 › 和 > 两种分隔符
                    if re.match(r'^[\s]*([✓✔√✘✗×\-x])?[\s]*\d+?\s*\[\w+\]\s*[›>]', next_line.strip()):
                        break
                    if next_line.strip() == '' and j > i + 1:
                        # 遇到空行，再检查一行
                        if j + 1 < len(lines) and not lines[j + 1].strip().startswith('Error:'):
                            break
                    if next_line.strip():
                        error_detail += next_line + "\n"
                    j += 1

            test_details.append({
                "name": test_name,
                "status": status,
                "detail": f"{browser} - {file_info}",
                "error": error_detail.strip() if error_detail else None
            })
            i = j if status == "fail" and error_detail else i + 1
            continue

        # 方法3: 解析行首的 ✓/✘ 标记（旧格式兼容）
        if re.match(r'^[✓✔√]', line):
            passed_count += 1
            test_name = re.sub(r'^[✓✔√]\s*', '', line)
            test_name = re.sub(r'\s*\([^)]*\)\s*$', '', test_name)
            test_details.append({"name": test_name, "status": "pass", "detail": line})
        elif re.match(r'^[✘✗×]', line):
            failed_count += 1
            test_name = re.sub(r'^[✘✗×]\s*', '', line)
            test_name = re.sub(r'\s*\([^)]*\)\s*$', '', test_name)
            test_details.append({"name": test_name, "status": "fail", "detail": line})

        i += 1

    # 如果方法2/3没有匹配到任何测试详情，使用方法1的总结行数据
    if not test_details and (summary_passed > 0 or summary_failed > 0):
        passed_count = summary_passed
        failed_count = summary_failed

    # 后备解析：如果以上都没匹配到，使用更智能的解析
    if passed_count == 0 and failed_count == 0:
        for line in stdout.splitlines():
            line_stripped = line.strip()
            # 只统计行首的明确标记
            if line_stripped.startswith('✓') or line_stripped.startswith('✔') or line_stripped.startswith('√'):
                passed_count += 1
            elif line_stripped.startswith('✘') or line_stripped.startswith('✗') or line_stripped.startswith('×'):
                failed_count += 1
            # 匹配 "[通过]" 或 "通过:" 等明确格式
            elif re.match(r'^[\[【]\s*通过\s*[\]】]', line_stripped) or re.match(r'^通过[：:]', line_stripped):
                passed_count += 1
            elif re.match(r'^[\[【]\s*失败\s*[\]】]', line_stripped) or re.match(r'^失败[：:]', line_stripped):
                failed_count += 1

    # 方法4: 解析菜单遍历/页面检查类测试输出（如「全量菜单遍历检查」）
    # 格式示例:
    #   "访问菜单: 首页 ✓"
    #   "检查页面: 商品管理 ✓"
    #   "菜单项: 订单管理 [通过]"
    #   "Page: Dashboard ✔"
    #   "导航: 用户管理 -> 成功"
    if passed_count == 0 and failed_count == 0:
        menu_check_pattern = re.compile(
            r'^\s*(?:访问菜单|检查页面|菜单项|页面|Page|导航|菜单|Menu|检查|Check|验证|Verify|跳转|Navigate)?\s*[：:]\s*(.+?)\s*(✓|✔|√|✘|✗|×|✅|❌|\[通过\]|\[失败\]|\[pass\]|\[fail\]|通过|失败|成功|success|failed|fail|error|ok|OK)\s*$',
            re.IGNORECASE
        )
        # 更宽松的后备模式：行尾有通过/失败标记
        loose_pass_pattern = re.compile(r'[✓✔√✅]|\[通过\]|通过$|成功$|success$|ok$|OK$', re.IGNORECASE)
        loose_fail_pattern = re.compile(r'[✘✗×❌]|\[失败\]|失败$|错误$|error$|failed$|fail$', re.IGNORECASE)

        for line in stdout.splitlines():
            line_stripped = line.strip()
            if not line_stripped:
                continue

            matched = False
            # 尝试精确匹配
            m = menu_check_pattern.match(line_stripped)
            if m:
                item_name = m.group(1).strip()
                status_mark = m.group(2).strip()
                if status_mark in ['✓', '✔', '√', '✅', '[通过]', '通过', '成功', 'success', 'ok', 'OK']:
                    passed_count += 1
                    test_details.append({"name": item_name, "status": "pass", "detail": line_stripped})
                else:
                    failed_count += 1
                    test_details.append({"name": item_name, "status": "fail", "detail": line_stripped})
                matched = True

            if not matched:
                # 尝试宽松匹配：检查行尾是否有通过/失败标记
                # 排除纯标记行（如只包含"✓"的行已在上面处理）
                if len(line_stripped) > 2:
                    if loose_pass_pattern.search(line_stripped):
                        passed_count += 1
                        # 提取名称：去掉行尾的标记
                        name = loose_pass_pattern.sub('', line_stripped).strip(' :-：')
                        test_details.append({"name": name or line_stripped, "status": "pass", "detail": line_stripped})
                        matched = True
                    elif loose_fail_pattern.search(line_stripped):
                        failed_count += 1
                        name = loose_fail_pattern.sub('', line_stripped).strip(' :-：')
                        test_details.append({"name": name or line_stripped, "status": "fail", "detail": line_stripped})
                        matched = True

    total_count = passed_count + failed_count

    return passed_count, failed_count, total_count, test_details


def _build_scenarios_html_from_details(test_details: List[Dict]) -> str:
    """从解析的测试详情构建场景 HTML"""
    if not test_details:
        return ""

    items = []
    for detail in test_details:
        status = detail.get("status", "pass")
        status_class = status
        status_text = {"pass": "✅ 通过", "fail": "❌ 失败", "partial": "⚠️ 部分通过", "skip": "⏭️ 跳过"}.get(status, "✅ 通过")
        name = detail.get("name", "未命名测试")
        info = detail.get("detail", "")
        error = detail.get("error", None)

        # 构建错误详情 HTML（如果有）
        error_html = ""
        if error:
            # 转义 HTML 特殊字符
            import html
            escaped_error = html.escape(error[:500])  # 限制错误信息长度
            if len(error) > 500:
                escaped_error += "..."
            error_html = f"""
      <div class="scenario-error">
        <div class="error-title">🔴 错误详情</div>
        <pre class="error-content">{escaped_error}</pre>
      </div>"""

        items.append(f"""
    <div class="scenario-card {status_class}">
      <div class="scenario-header">
        <span class="scenario-name">{name}</span>
        <span class="status-tag {status_class}">{status_text}</span>
      </div>
      <div class="scenario-detail">{info}</div>
      {error_html}
    </div>""")

    return f"""
  <!-- Test Scenarios -->
  <div class="section-title">📋 测试用例 ({len(test_details)} 个)</div>
  <div class="scenario-grid">
    {''.join(items)}
  </div>"""


def _collect_screenshots(screenshots: Optional[List[str]], execution_result: Dict[str, Any]) -> List[str]:
    """收集截图路径，从多个来源尝试获取"""
    all_screenshots = []

    # 来源1: 直接传入的截图列表
    if screenshots:
        all_screenshots.extend(screenshots)

    # 来源2: 从 execution_result 中解析截图信息
    stdout = execution_result.get("stdout", "")
    if stdout:
        # 查找 stdout 中提到的截图路径
        import re
        # 匹配常见的截图路径模式
        screenshot_patterns = [
            r'screenshot[\w\s]*:\s*([\w\-/\\.]+\.png)',
            r'截图[\w\s]*:\s*([\w\-/\\.]+\.png)',
            r'([\w\-/\\]+screenshots[\w\-/\\]*\.png)',
        ]
        for pattern in screenshot_patterns:
            matches = re.findall(pattern, stdout, re.IGNORECASE)
            for match in matches:
                if match not in all_screenshots:
                    all_screenshots.append(match)

    # 来源3: 从 stderr 中查找截图信息
    stderr = execution_result.get("stderr", "")
    if stderr:
        import re
        matches = re.findall(r'([\w\-/\\]+\.png)', stderr)
        for match in matches:
            if match not in all_screenshots:
                all_screenshots.append(match)

    return all_screenshots


def _parse_self_reflect_json(report_dir: Optional[str]) -> Optional[tuple]:
    """解析 webwright 脚本生成的 self_reflect_result.json 文件

    Args:
        report_dir: 报告目录路径

    Returns:
        Optional[tuple]: (passed_count, failed_count, total_count, test_details) 或 None
    """
    if not report_dir:
        return None

    try:
        sr_path = Path(report_dir) / "self_reflect_result.json"
        if not sr_path.exists():
            # 也尝试在父目录查找（因为 report_dir 可能是 run_XXX 的子目录）
            sr_path = Path(report_dir).parent / "self_reflect_result.json"
            if not sr_path.exists():
                return None

        data = json.loads(sr_path.read_text(encoding="utf-8"))
        results = data.get("results", [])
        summary = data.get("summary", {})

        if not results:
            return None

        # 统计通过/失败数
        passed = summary.get("PASS", 0)
        failed = summary.get("FAIL", 0)
        warn = summary.get("WARN", 0)
        total = passed + failed + warn

        # 构建测试详情
        test_details = []
        for r in results:
            status = r.get("status", "PASS")
            # 将 PASS/WARN/FAIL 映射到 pass/fail
            if status == "PASS":
                mapped_status = "pass"
            elif status == "FAIL":
                mapped_status = "fail"
            elif status == "WARN":
                mapped_status = "partial"  # WARN 映射为 partial
            else:
                mapped_status = "pass"

            test_details.append({
                "name": r.get("name", "未命名检查点"),
                "status": mapped_status,
                "detail": f"检查点: {r.get('cp', '')} | {r.get('detail', '')}".strip(" |"),
                "error": None,
            })

        print(f"[Web Report] 从 self_reflect_result.json 解析到 {total} 个检查点 (PASS={passed}, WARN={warn}, FAIL={failed})")
        return passed, failed, total, test_details

    except Exception as e:
        print(f"[Web Report] 解析 self_reflect_result.json 失败: {e}")
        return None
