"""
Security Agent 工具模块

本目录包含所有渗透测试智能体的工具定义，按功能分类组织：
- 信息收集 (recon)
- 漏洞利用 (exploit)
- 报告生成 (report)
- 结果存储 (storage)
- 管理接口 (mgmt)
"""

from typing import List
from langchain_core.tools import BaseTool

from app.agents.tools.security.recon_tools import (
    recon_subdomains,
    recon_port_scan,
    recon_directory_scan,
    recon_fingerprint,
    recon_full_scan,
)

from app.agents.tools.security.exploit_tools import (
    exploit_sqli,
    exploit_xss,
    exploit_lfi,
    exploit_file_download,
    exploit_full_scan,
)

from app.agents.tools.security.page_api_discovery import (
    discover_apis_from_page,
)

from app.agents.tools.security.web_security_tools import (
    scan_web_xss,
    scan_web_csrf,
    scan_web_clickjacking,
    scan_web_sensitive_info,
)

from app.agents.tools.security.api_security_tools import (
    api_auth_bypass_test,
    api_idor_test,
    api_input_validation_test,
    api_rate_limit_test,
)

from app.agents.tools.security.report_tools import (
    generate_pentest_report,
    generate_executive_summary,
    generate_html_pentest_report,
)

from app.agents.tools.security.storage_tools import (
    storage_create_project,
    storage_list_projects,
    storage_add_vulnerability,
    storage_list_vulnerabilities,
    storage_update_vulnerability_status,
    storage_save_scan_result,
    storage_get_scan_results,
    storage_save_report,
    storage_get_statistics,
)

from app.agents.tools.security.pentest_mgmt_tools import (
    mgmt_create_pentest,
    mgmt_list_pentests,
    mgmt_save_pentest_report,
    mgmt_list_reports,
    mgmt_add_vulnerability,
    mgmt_list_vulnerabilities,
    mgmt_update_pentest_status,
    mgmt_update_pentest_thread_id,
)

# 按业务域分类的工具列表
RECON_TOOLS = [
    recon_subdomains,
    recon_port_scan,
    recon_directory_scan,
    recon_fingerprint,
    recon_full_scan,
]

EXPLOIT_TOOLS = [
    exploit_sqli,
    exploit_xss,
    exploit_lfi,
    exploit_file_download,
    exploit_full_scan,
]

PAGE_DISCOVERY_TOOLS = [
    discover_apis_from_page,
]

WEB_SECURITY_TOOLS = [
    scan_web_xss,
    scan_web_csrf,
    scan_web_clickjacking,
    scan_web_sensitive_info,
]

API_SECURITY_TOOLS = [
    api_auth_bypass_test,
    api_idor_test,
    api_input_validation_test,
    api_rate_limit_test,
]

REPORT_TOOLS = [
    generate_pentest_report,
    generate_executive_summary,
    generate_html_pentest_report,
]

STORAGE_TOOLS = [
    storage_create_project,
    storage_list_projects,
    storage_add_vulnerability,
    storage_list_vulnerabilities,
    storage_update_vulnerability_status,
    storage_save_scan_result,
    storage_get_scan_results,
    storage_save_report,
    storage_get_statistics,
]

MGMT_TOOLS = [
    mgmt_create_pentest,
    mgmt_list_pentests,
    mgmt_save_pentest_report,
    mgmt_list_reports,
    mgmt_add_vulnerability,
    mgmt_list_vulnerabilities,
    mgmt_update_pentest_status,
    mgmt_update_pentest_thread_id,
]

ALL_SECURITY_TOOLS = (
    RECON_TOOLS
    + EXPLOIT_TOOLS
    + PAGE_DISCOVERY_TOOLS
    + WEB_SECURITY_TOOLS
    + API_SECURITY_TOOLS
    + REPORT_TOOLS
    + STORAGE_TOOLS
    + MGMT_TOOLS
)


def get_local_tools() -> List[BaseTool]:
    """
    获取所有 Security 本地工具列表。

    MCP 工具（如 Chart MCP Server）在 agent.py 中异步加载，此处只返回本地工具。
    """
    return list(ALL_SECURITY_TOOLS)


__all__ = [
    # 信息收集
    "recon_subdomains",
    "recon_port_scan",
    "recon_directory_scan",
    "recon_fingerprint",
    "recon_full_scan",
    # 漏洞利用
    "exploit_sqli",
    "exploit_xss",
    "exploit_lfi",
    "exploit_file_download",
    "exploit_full_scan",
    # 页面驱动接口发现
    "discover_apis_from_page",
    # Web 安全扫描
    "scan_web_xss",
    "scan_web_csrf",
    "scan_web_clickjacking",
    "scan_web_sensitive_info",
    # API 安全扫描
    "api_auth_bypass_test",
    "api_idor_test",
    "api_input_validation_test",
    "api_rate_limit_test",
    # 报告生成
    "generate_pentest_report",
    "generate_executive_summary",
    "generate_html_pentest_report",
    # 结果存储
    "storage_create_project",
    "storage_list_projects",
    "storage_add_vulnerability",
    "storage_list_vulnerabilities",
    "storage_update_vulnerability_status",
    "storage_save_scan_result",
    "storage_get_scan_results",
    "storage_save_report",
    "storage_get_statistics",
    # 管理接口
    "mgmt_create_pentest",
    "mgmt_list_pentests",
    "mgmt_save_pentest_report",
    "mgmt_list_reports",
    "mgmt_add_vulnerability",
    "mgmt_list_vulnerabilities",
    "mgmt_update_pentest_status",
    "mgmt_update_pentest_thread_id",
    # 分类列表
    "RECON_TOOLS",
    "EXPLOIT_TOOLS",
    "PAGE_DISCOVERY_TOOLS",
    "WEB_SECURITY_TOOLS",
    "API_SECURITY_TOOLS",
    "REPORT_TOOLS",
    "STORAGE_TOOLS",
    "MGMT_TOOLS",
    "ALL_SECURITY_TOOLS",
    "get_local_tools",
]
