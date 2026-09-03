"""
缺陷报告服务

负责：
- 在 API 测试报告中增加"IDP 缺陷登记结果"章节
- 支持状态：已登记 / 已存在，已关联 / 跳过 / 项目未匹配 / 登记失败
- 回写 IDP 编号和链接到测试报告
"""

import html
import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.idp_defect_repo import IDPDefectRecordRepository

logger = logging.getLogger(__name__)


class DefectReportService:
    """
    缺陷报告服务

    处理 IDP 缺陷登记结果在测试报告中的回写
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.record_repo = IDPDefectRecordRepository(session)

    async def get_defect_summary_for_run(
        self,
        test_run_id: UUID,
    ) -> dict:
        """
        获取指定测试运行的 IDP 缺陷登记汇总

        Args:
            test_run_id: 测试运行 ID

        Returns:
            dict: 汇总信息
        """
        records = await self.record_repo.get_by_test_run(test_run_id)

        summary = {
            "total": len(records),
            "not_required": 0,
            "insufficient_evidence": 0,
            "pending": 0,
            "created": 0,
            "verified": 0,
            "written_back": 0,
            "sync_failed": 0,
            "duplicate": 0,
            "skipped": 0,
            "items": [],
        }

        for record in records:
            status = record.create_status
            if status in summary:
                summary[status] += 1

            # 构建显示项
            item = {
                "defect_title": record.defect_title or "未知缺陷",
                "idp_issue_key": record.idp_issue_key,
                "idp_issue_url": record.idp_issue_url,
                "status": self._status_to_display(status),
                "status_code": status,
                "reqid": record.reqid,
                "error_message": record.error_message,
            }
            summary["items"].append(item)

        return summary

    def _status_to_display(self, status: str) -> str:
        """将状态转换为显示文本"""
        status_map = {
            "not_required": "无需登记",
            "insufficient_evidence": "证据不足",
            "pending": "待处理",
            "created": "已登记",
            "verified": "已校验",
            "written_back": "已回写",
            "sync_failed": "同步失败",
            "duplicate": "已存在，已关联",
            "skipped": "跳过",
        }
        return status_map.get(status, status)

    def generate_markdown_section(self, summary: dict) -> str:
        """
        生成 Markdown 格式的 IDP 缺陷登记结果章节

        用于嵌入到测试报告中
        """
        lines = [
            "## IDP 缺陷登记结果",
            "",
            f"总计: {summary['total']} | 已登记: {summary['created']} | 已校验: {summary['verified']} | 已回写: {summary['written_back']} | 同步失败: {summary['sync_failed']} | 已存在: {summary['duplicate']} | 跳过: {summary['skipped']} | 待处理: {summary['pending']} | 证据不足: {summary['insufficient_evidence']}",
            "",
            "| 缺陷标题 | IDP 编号 | 链接 | 状态 |",
            "|---|---|---|---|",
        ]

        for item in summary["items"]:
            title = item["defect_title"] or "-"
            issue_key = item["idp_issue_key"] or "-"
            link = item["idp_issue_url"] or "-"
            status = item["status"]

            # 如果有链接，生成 Markdown 链接
            if link != "-":
                link_md = f"[查看]({link})"
            else:
                link_md = "-"

            lines.append(f"| {title} | {issue_key} | {link_md} | {status} |")

        lines.append("")
        return "\n".join(lines)

    def generate_html_section(self, summary: dict) -> str:
        """
        生成 HTML 格式的 IDP 缺陷登记结果章节

        用于嵌入到 HTML 测试报告中。
        所有动态内容均经过 html.escape() 处理，防止 XSS。
        """
        rows = []
        for item in summary["items"]:
            # 对所有动态内容进行 HTML 转义，防止 XSS
            title = html.escape(item["defect_title"] or "-")
            issue_key = html.escape(item["idp_issue_key"] or "-")
            link = item["idp_issue_url"]
            status = html.escape(item["status"])

            # 状态颜色
            status_class = self._status_to_css_class(item["status_code"])

            # 链接也进行转义
            if link:
                link_html = f'<a href="{html.escape(link)}" target="_blank">查看</a>'
            else:
                link_html = "-"

            rows.append(f"""
                <tr>
                    <td>{title}</td>
                    <td>{issue_key}</td>
                    <td>{link_html}</td>
                    <td><span class="badge {status_class}">{status}</span></td>
                </tr>
            """)

        rows_html = "\n".join(rows) if rows else "<tr><td colspan=\"4\" class=\"text-center\">无记录</td></tr>"

        return f"""
        <div class="idp-defect-section">
            <h2>IDP 缺陷登记结果</h2>
            <div class="summary">
                总计: {summary['total']} |
                已登记: {summary['created']} |
                已校验: {summary['verified']} |
                已回写: {summary['written_back']} |
                同步失败: {summary['sync_failed']} |
                已存在: {summary['duplicate']} |
                跳过: {summary['skipped']} |
                待处理: {summary['pending']} |
                证据不足: {summary['insufficient_evidence']}
            </div>
            <table class="table">
                <thead>
                    <tr>
                        <th>缺陷标题</th>
                        <th>IDP 编号</th>
                        <th>链接</th>
                        <th>状态</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>
        </div>
        """

    def _status_to_css_class(self, status: str) -> str:
        """状态对应的 CSS 类名"""
        class_map = {
            "not_required": "badge-light",
            "insufficient_evidence": "badge-warning",
            "pending": "badge-secondary",
            "created": "badge-success",
            "verified": "badge-success",
            "written_back": "badge-success",
            "sync_failed": "badge-danger",
            "duplicate": "badge-info",
            "skipped": "badge-warning",
        }
        return class_map.get(status, "badge-secondary")

    async def append_to_report(
        self,
        test_run_id: UUID,
        report_content: str,
        report_format: str = "markdown",
    ) -> str:
        """
        将 IDP 缺陷登记结果追加到测试报告

        Args:
            test_run_id: 测试运行 ID
            report_content: 原始报告内容
            report_format: 报告格式 (markdown/html)

        Returns:
            str: 追加后的报告内容
        """
        summary = await self.get_defect_summary_for_run(test_run_id)

        if summary["total"] == 0:
            return report_content

        if report_format == "html":
            section = self.generate_html_section(summary)
            # 在 </body> 前插入
            if "</body>" in report_content:
                return report_content.replace("</body>", f"{section}\n</body>")
            else:
                return report_content + "\n" + section
        else:
            section = self.generate_markdown_section(summary)
            return report_content + "\n\n" + section
