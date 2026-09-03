"""
安全测试管理 API

提供安全测试（渗透测试）的 CRUD 操作接口
"""

from typing import Optional
from uuid import UUID

from datetime import datetime, timedelta

from fastapi import APIRouter, Query, Form, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.deps import (
    PaginationDep,
    get_security_test_service,
)
from app.schemas.common import SuccessResponse
from app.services.security_test_service import SecurityTestService
from app.config.minio_client import MinIOClient


class UpdateStatusRequest(BaseModel):
    status: str


class UpdateThreadIdRequest(BaseModel):
    thread_id: str


class UpdateSecurityTestRequest(BaseModel):
    name: Optional[str] = None
    target: Optional[str] = None
    description: Optional[str] = None


router = APIRouter(prefix="/projects/{project_identifier}/security-tests")


# ============ 安全测试任务管理接口 ============

@router.post(
    "",
    response_model=SuccessResponse,
    summary="创建安全测试任务",
    description="创建新的渗透测试任务",
)
async def create_security_test(
    project_identifier: str,
    service: SecurityTestService = Depends(get_security_test_service),
    name: str = Form(..., description="安全测试名称"),
    target: str = Form(..., description="测试目标（URL/IP/域名）"),
    description: Optional[str] = Form(None, description="描述"),
    scan_config: Optional[str] = Form(None, description="扫描配置 (JSON)"),
    folder_id: Optional[str] = Form(None, description="文件夹 ID"),
):
    """创建安全测试任务"""
    import json
    config = json.loads(scan_config) if scan_config else None

    result = await service.create_security_test(
        project_identifier=project_identifier,
        name=name,
        target=target,
        description=description,
        scan_config=config,
        folder_id=folder_id,
    )

    return SuccessResponse(data=result, message="安全测试任务创建成功")


@router.get(
    "",
    response_model=SuccessResponse,
    summary="获取安全测试列表",
    description="获取项目下的所有安全测试列表，支持搜索和过滤",
)
async def list_security_tests(
    project_identifier: str,
    service: SecurityTestService = Depends(get_security_test_service),
    pagination: PaginationDep = None,  # type: ignore
    search: Optional[str] = Query(None, description="搜索关键词"),
    status: Optional[str] = Query(None, description="状态过滤"),
):
    """获取安全测试列表"""
    result = await service.list_security_tests(
        project_identifier=project_identifier,
        page=pagination.p,
        page_size=pagination.page_size,
        search=search,
        status=status,
    )

    return SuccessResponse(data=result)


@router.get(
    "/{security_test_id}",
    response_model=SuccessResponse,
    summary="获取安全测试详情",
    description="获取指定安全测试的详细信息",
)
async def get_security_test(
    project_identifier: str,
    security_test_id: str,
    service: SecurityTestService = Depends(get_security_test_service),
):
    """获取安全测试详情"""
    result = await service.get_security_test(
        project_identifier=project_identifier,
        security_test_id=security_test_id,
    )

    return SuccessResponse(data=result)


@router.patch(
    "/{security_test_id}/status",
    response_model=SuccessResponse,
    summary="更新安全测试状态",
    description="更新安全测试任务的状态",
)
async def update_security_test_status(
    project_identifier: str,
    security_test_id: str,
    request: UpdateStatusRequest,
    service: SecurityTestService = Depends(get_security_test_service),
):
    """更新安全测试状态"""
    result = await service.update_security_test_status(
        project_identifier=project_identifier,
        security_test_id=security_test_id,
        status=request.status,
    )

    return SuccessResponse(data=result, message="状态更新成功")


@router.patch(
    "/{security_test_id}/thread-id",
    response_model=SuccessResponse,
    summary="更新安全测试关联的对话线程 ID",
    description="更新安全测试任务关联的 LangGraph 对话线程 ID，用于恢复历史对话",
)
async def update_security_test_thread_id(
    project_identifier: str,
    security_test_id: str,
    request: UpdateThreadIdRequest,
    service: SecurityTestService = Depends(get_security_test_service),
):
    """更新安全测试关联的对话线程 ID"""
    result = await service.update_security_test_thread_id(
        project_identifier=project_identifier,
        security_test_id=security_test_id,
        thread_id=request.thread_id,
    )

    return SuccessResponse(data=result, message="对话线程 ID 更新成功")


@router.patch(
    "/{security_test_id}",
    response_model=SuccessResponse,
    summary="更新安全测试任务",
    description="更新安全测试任务的基本信息（名称、目标、描述）",
)
async def update_security_test(
    project_identifier: str,
    security_test_id: str,
    request: UpdateSecurityTestRequest,
    service: SecurityTestService = Depends(get_security_test_service),
):
    """更新安全测试任务基本信息"""
    result = await service.update_security_test(
        project_identifier=project_identifier,
        security_test_id=security_test_id,
        name=request.name,
        target=request.target,
        description=request.description,
    )

    return SuccessResponse(data=result, message="渗透测试任务更新成功")


@router.delete(
    "/{security_test_id}",
    response_model=SuccessResponse,
    summary="删除安全测试",
    description="删除指定的安全测试任务及其关联数据",
)
async def delete_security_test(
    project_identifier: str,
    security_test_id: str,
    service: SecurityTestService = Depends(get_security_test_service),
):
    """删除安全测试"""
    result = await service.delete_security_test(
        project_identifier=project_identifier,
        security_test_id=security_test_id,
    )

    return SuccessResponse(data=result)


@router.get(
    "/reports/{report_id}",
    response_model=SuccessResponse,
    summary="获取报告详情",
    description="获取指定报告的详细信息",
)
async def get_report(
    project_identifier: str,
    report_id: str,
    service: SecurityTestService = Depends(get_security_test_service),
):
    """获取报告详情"""
    result = await service.get_report(
        project_identifier=project_identifier,
        report_id=report_id,
    )
    return SuccessResponse(data=result)


@router.get(
    "/reports/{report_id}/download",
    summary="下载报告文件",
    description="获取报告的下载链接（7天有效）",
)
async def download_report(
    project_identifier: str,
    report_id: str,
    service: SecurityTestService = Depends(get_security_test_service),
):
    """下载报告文件 - 获取7天有效的预签名下载链接"""
    report = await service.get_report(
        project_identifier=project_identifier,
        report_id=report_id,
    )

    if not report.get("file_path"):
        raise HTTPException(status_code=404, detail="报告文件不存在")

    try:
        url = MinIOClient.get_presigned_url(
            report["file_path"],
            expires=timedelta(days=7),
        )
        return {
            "download_url": url,
            "expires_in": 604800,  # 7天 = 7 * 24 * 60 * 60 秒
            "expires_at": (datetime.utcnow() + timedelta(days=7)).isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成下载链接失败: {str(e)}")


@router.post(
    "/reports/{report_id}/refresh-url",
    summary="刷新报告下载链接",
    description="重新生成报告的预签名下载链接（7天有效），用于旧链接过期后重新获取",
)
async def refresh_report_url(
    project_identifier: str,
    report_id: str,
    service: SecurityTestService = Depends(get_security_test_service),
):
    """刷新报告下载链接 - 重新生成7天有效的预签名URL"""
    report = await service.get_report(
        project_identifier=project_identifier,
        report_id=report_id,
    )

    if not report.get("file_path"):
        raise HTTPException(status_code=404, detail="报告文件不存在")

    try:
        url = MinIOClient.get_presigned_url(
            report["file_path"],
            expires=timedelta(days=7),
        )
        return SuccessResponse(
            data={
                "download_url": url,
                "expires_in": 604800,  # 7天 = 7 * 24 * 60 * 60 秒
                "expires_at": (datetime.utcnow() + timedelta(days=7)).isoformat(),
                "report_id": report_id,
                "file_path": report["file_path"],
            },
            message="下载链接已刷新，有效期7天",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"刷新下载链接失败: {str(e)}")


@router.get(
    "/reports/{report_id}/view",
    summary="代理查看报告",
    description="通过后端代理从 MinIO 读取报告内容并返回，不暴露 MinIO 直链地址",
)
async def view_report(
    project_identifier: str,
    report_id: str,
    service: SecurityTestService = Depends(get_security_test_service),
):
    """代理查看报告 - 后端从 MinIO 读取内容直接返回，前端不接触 MinIO 地址"""
    report = await service.get_report(
        project_identifier=project_identifier,
        report_id=report_id,
    )

    if not report.get("file_path"):
        raise HTTPException(status_code=404, detail="报告文件不存在")

    try:
        # 从 MinIO 下载文件内容
        content = MinIOClient.download_file(report["file_path"])

        # 根据文件路径判断内容类型
        file_path = report["file_path"]
        if file_path.endswith(".html"):
            media_type = "text/html; charset=utf-8"
        elif file_path.endswith(".md"):
            media_type = "text/markdown; charset=utf-8"
        elif file_path.endswith(".json"):
            media_type = "application/json; charset=utf-8"
        else:
            media_type = "text/plain; charset=utf-8"

        return Response(
            content=content,
            media_type=media_type,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取报告失败: {str(e)}")


# ============ 漏洞管理接口 ============

@router.post(
    "/{security_test_id}/vulnerabilities",
    response_model=SuccessResponse,
    summary="添加漏洞发现",
    description="向安全测试任务添加漏洞发现记录",
)
async def add_vulnerability(
    project_identifier: str,
    security_test_id: str,
    service: SecurityTestService = Depends(get_security_test_service),
    vuln_id: str = Form(..., description="漏洞编号，如 VL-001"),
    title: str = Form(..., description="漏洞标题"),
    severity: str = Form(..., description="风险等级: Critical/High/Medium/Low/Info"),
    vuln_type: Optional[str] = Form(None, description="漏洞类型"),
    affected_url: Optional[str] = Form(None, description="受影响 URL"),
    parameter: Optional[str] = Form(None, description="受影响参数"),
    description: Optional[str] = Form(None, description="漏洞描述"),
    reproduction: Optional[str] = Form(None, description="复现步骤"),
    evidence: Optional[str] = Form(None, description="证据"),
    remediation: Optional[str] = Form(None, description="修复建议"),
    cvss_score: Optional[float] = Form(None, description="CVSS 评分"),
):
    """添加漏洞发现"""
    result = await service.add_vulnerability(
        project_identifier=project_identifier,
        security_test_id=security_test_id,
        vuln_id=vuln_id,
        title=title,
        severity=severity,
        vuln_type=vuln_type,
        affected_url=affected_url,
        parameter=parameter,
        description=description,
        reproduction=reproduction,
        evidence=evidence,
        remediation=remediation,
        cvss_score=cvss_score,
    )

    return SuccessResponse(data=result, message="漏洞添加成功")


@router.get(
    "/{security_test_id}/vulnerabilities",
    response_model=SuccessResponse,
    summary="获取漏洞列表",
    description="获取安全测试任务下的所有漏洞发现",
)
async def list_vulnerabilities(
    project_identifier: str,
    security_test_id: str,
    service: SecurityTestService = Depends(get_security_test_service),
    pagination: PaginationDep = None,  # type: ignore
    severity: Optional[str] = Query(None, description="按风险等级过滤"),
    status: Optional[str] = Query(None, description="按状态过滤"),
):
    """获取漏洞列表"""
    result = await service.list_vulnerabilities(
        project_identifier=project_identifier,
        security_test_id=security_test_id,
        page=pagination.p,
        page_size=pagination.page_size,
        severity=severity,
        status=status,
    )

    return SuccessResponse(data=result)


# ============ 报告管理接口 ============

@router.post(
    "/{security_test_id}/reports",
    response_model=SuccessResponse,
    summary="保存渗透测试报告",
    description="保存生成的渗透测试报告",
)
async def save_report(
    project_identifier: str,
    security_test_id: str,
    service: SecurityTestService = Depends(get_security_test_service),
    name: str = Form(..., description="报告名称"),
    report_type: str = Form(default="full", description="报告类型: full/executive/technical"),
    format: str = Form(default="markdown", description="报告格式: markdown/html/json"),
    content: str = Form(..., description="报告完整内容"),
    file_path: Optional[str] = Form(None, description="MinIO 文件路径"),
    risk_score: Optional[float] = Form(None, description="风险评分"),
    summary: Optional[str] = Form(None, description="摘要数据 (JSON)"),
):
    """保存渗透测试报告"""
    result = await service.save_report(
        project_identifier=project_identifier,
        security_test_id=security_test_id,
        name=name,
        report_type=report_type,
        format=format,
        content=content,
        file_path=file_path,
        risk_score=risk_score,
        summary=summary,
    )

    return SuccessResponse(data=result, message="报告保存成功")


@router.get(
    "/{security_test_id}/reports",
    response_model=SuccessResponse,
    summary="获取报告列表",
    description="获取安全测试任务下的所有报告",
)
async def list_reports(
    project_identifier: str,
    security_test_id: str,
    service: SecurityTestService = Depends(get_security_test_service),
    pagination: PaginationDep = None,  # type: ignore
):
    """获取报告列表"""
    result = await service.list_reports(
        project_identifier=project_identifier,
        security_test_id=security_test_id,
        page=pagination.p,
        page_size=pagination.page_size,
    )

    return SuccessResponse(data=result)
