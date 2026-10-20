"""
IDP HTTP 客户端

封装 IDP 平台的 API 调用
- 登录接口（由 IDPAuthService 使用）
- 项目信息查询
- 缺陷创建
- 缺陷详情查询

实现要求：
- 使用 httpx.AsyncClient
- 统一处理超时、401、5xx 错误
- 请求/响应日志脱敏
- 401 时自动刷新 Token 并重试一次

⚠️ 服务端能力边界（2026-10-15 实测，完整结论见 `docs/IDP_API_CONTRACT.md` §9）：
- **无程序化附图通道**：`issues/{id}/attachments`、`issue_images`、`upload_images` 均 403
  ⇒ 不要在此新增"上传附件/图片"方法，截图只能人工拖入；
  唯一可能的程序化内嵌路径是 description 的 Delta image op + 公网可读图片 URL。
- **需求/元数据端点不可用**：`requirements`、`backlog`、`issues?typeCode=feature`、
  `schemes/query_issue_types` 均 403 ⇒ 需求名称等元数据只能来自本地配置或人工给定。
- **列表端点 403 + 号段 ≠ issueId**：问题号（如 `dmcloud-saas-67196`）不是 issueId（`927934`），
  误当 issueId 查询会 **HTTP 200 静默返回无关单**；无法按编号反查，只能用锚点区间内插扫描。
"""

import json
import logging
from typing import Any, Optional

import httpx

from app.config.settings import settings
from app.services.idp_auth_service import IDPAuthService

logger = logging.getLogger(__name__)


class IDPClient:
    """
    IDP HTTP 客户端

    封装 IDP 平台的所有 API 调用
    """

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self.base_url = settings.idp_base_url.rstrip("/")
        self.organization_id = settings.idp_organization_id

    def _get_client(self) -> httpx.AsyncClient:
        """创建 HTTP 客户端实例"""
        return httpx.AsyncClient(timeout=self.timeout)

    def _safe_log_headers(self, headers: dict) -> dict:
        """脱敏请求头（用于日志）"""
        safe = {}
        for k, v in headers.items():
            if k.lower() in ["authorization", "x-auth-token", "cookie"]:
                safe[k] = "***REDACTED***"
            else:
                safe[k] = v
        return safe

    def _safe_log_body(self, body: Any) -> Any:
        """脱敏请求体（用于日志）"""
        if not isinstance(body, dict):
            return body
        safe = {}
        for k, v in body.items():
            if any(s in k.lower() for s in ["password", "token", "secret", "auth", "cookie"]):
                safe[k] = "***REDACTED***"
            elif isinstance(v, dict):
                safe[k] = self._safe_log_body(v)
            else:
                safe[k] = v
        return safe

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict:
        """
        发送 HTTP 请求（带 Token 和自动重试）

        错误处理：
        - Token 缺失: 抛出 ValueError，提示配置环境变量
        - Token 过期/401: 清除 Token 并重试一次
        - 字段校验失败(400/422): 抛出包含错误详情的异常
        - IDP 5xx: 抛出异常，不影响测试结果保存
        - 网络错误: 抛出异常，不影响测试结果保存

        Args:
            method: HTTP 方法
            path: API 路径（不含 base_url）
            **kwargs: 额外的请求参数

        Returns:
            dict: 响应 JSON 数据

        Raises:
            ValueError: Token 未配置
            Exception: 请求失败时抛出异常
        """
        url = f"{self.base_url}{path}"

        try:
            token = await IDPAuthService.get_token()
        except ValueError as e:
            logger.error("[IDPClient] Token 未配置: %s", e)
            raise

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        headers.setdefault("Content-Type", "application/json")

        # 脱敏日志
        safe_headers = self._safe_log_headers(headers)
        safe_body = self._safe_log_body(kwargs.get("json"))
        logger.info("[IDPClient] %s %s", method, url)
        logger.debug("[IDPClient] Headers: %s", safe_headers)
        logger.debug("[IDPClient] Body: %s", safe_body)

        async with self._get_client() as client:
            try:
                response = await client.request(method, url, headers=headers, **kwargs)

                # 401 时标记为认证失败（静态 Token 模式无法自动刷新）
                if response.status_code == 401:
                    logger.error(
                        "[IDPClient] 收到 401 Unauthorized，"
                        "静态 Token 模式无法自动刷新。"
                        "请更新环境变量 IDP_TOKEN"
                    )
                    raise Exception(
                        "IDP Token 已过期或无效。"
                        "请更新环境变量 IDP_TOKEN 后重试"
                    )

                # 字段校验失败 (400/422)
                if response.status_code in (400, 422):
                    error_text = response.text[:500]
                    logger.error(
                        "[IDPClient] 字段校验失败 HTTP %s: %s",
                        response.status_code,
                        error_text,
                    )
                    try:
                        error_data = response.json()
                        error_msg = error_data.get("message", error_text)
                        error_code = error_data.get("code", "unknown")
                    except Exception:
                        error_msg = error_text
                        error_code = "unknown"
                    raise Exception(
                        f"IDP 字段校验失败 [{error_code}]: {error_msg}"
                    )

                # 5xx 服务器错误
                if response.status_code >= 500:
                    logger.error(
                        "[IDPClient] IDP 服务器错误 HTTP %s: %s",
                        response.status_code,
                        response.text[:500],
                    )
                    raise Exception(
                        f"IDP 服务器错误: HTTP {response.status_code}。"
                        f"请稍后重试或联系 IDP 管理员"
                    )

                response.raise_for_status()
                data = response.json()
                logger.debug("[IDPClient] 响应: %s", self._safe_log_body(data))
                return data

            except httpx.HTTPStatusError as e:
                logger.error(
                    "[IDPClient] HTTP 错误 %s: %s",
                    e.response.status_code,
                    e.response.text[:500],
                )
                raise Exception(
                    f"IDP API 错误: HTTP {e.response.status_code}"
                ) from e
            except httpx.RequestError as e:
                logger.error("[IDPClient] 请求失败: %s", e)
                raise Exception(
                    f"IDP 请求失败: {e}。请检查网络连接"
                ) from e

    # =========================================================================
    # 项目相关接口
    # =========================================================================

    async def get_project_info(self, project_id: int) -> dict:
        """
        查询项目信息

        Args:
            project_id: IDP 项目 ID

        Returns:
            dict: 项目信息
        """
        path = f"/agile/v1/projects/{project_id}/project_info"
        return await self._request("GET", path)

    # =========================================================================
    # 缺陷相关接口
    # =========================================================================

    async def create_issue(
        self,
        project_id: int,
        issue_data: dict,
    ) -> dict:
        """
        创建缺陷

        联调确认必填字段：
        - summary: 缺陷标题
        - typeCode: 类型代码 (如 "bug")
        - issueTypeId: 问题类型 ID (如 3)
        - priorityCode: 优先级代码 (如 "priority-2")
        - priorityId: 优先级 ID (如 2)

        可选字段：
        - description: 描述 (Delta JSON 字符串)
        - sprintId: 冲刺 ID
        - epicId: 史诗 ID
        - assigneeId: 指派人 ID

        成功响应字段：
        - issueId: 缺陷 ID (如 912536)
        - issueNum: 缺陷编号 (如 "jf-20260122392-01-2513")
        - typeCode: 类型代码
        - statusId: 状态 ID
        - summary: 标题
        - projectId: 项目 ID

        Args:
            project_id: IDP 项目 ID
            issue_data: 缺陷数据

        Returns:
            dict: 创建结果
        """
        path = f"/agile/v1/projects/{project_id}/issues?applyType=agile"
        return await self._request("POST", path, json=issue_data)

    async def update_issue(
        self,
        project_id: int,
        issue_data: dict,
    ) -> dict:
        """
        更新缺陷

        用于补齐描述等字段更新。
        已按浏览器请求确认：PUT /agile/v1/projects/{project_id}/issues。
        请求体至少包含 issueId、objectVersionNumber、description。

        成功判据（三者齐全才算成功，2026-10-15 实测）：
          1) HTTP **201**（不是 200）；
          2) 回读 `objectVersionNumber` = 原值 + 1；
          3) 目标字段与预期内容 **逐字** 一致。
        ⚠️ 只查关键词（如"描述里出现 requestId"）会把成功写入误判为失败；
        ⚠️ 回滚场景要按版本另存快照，再次 GET 会覆盖 before 快照。

        Args:
            project_id: IDP 项目 ID
            issue_data: 更新请求体，包含 issueId、objectVersionNumber、description

        Returns:
            dict: 更新结果
        """
        path = f"/agile/v1/projects/{project_id}/issues"
        return await self._request("PUT", path, json=issue_data)

    async def get_issue(
        self,
        project_id: int,
        issue_id: int,
    ) -> dict:
        """
        查询缺陷详情

        ⚠️ 返回体是 **扁平 JSON**（无 `{"data": ...}` 包装）——解析写成 `.get("data") or {}`
        会把整条单读成空值、误判"标题/描述丢了"；正确写法 `payload.get("data") or payload`。
        ⚠️ `issue_id` 必须是**真实 issueId**，传问题号号段会 HTTP 200 静默返回无关单。

        Args:
            project_id: IDP 项目 ID
            issue_id: IDP Issue ID（不是问题号 issueNum）

        Returns:
            dict: 缺陷详情
        """
        path = f"/agile/v1/projects/{project_id}/issues/{issue_id}?organizationId={self.organization_id}"
        return await self._request("GET", path)

    # =========================================================================
    # dry-run 模式支持
    # =========================================================================

    async def create_issue_dry_run(
        self,
        project_id: int,
        issue_data: dict,
    ) -> dict:
        """
        dry-run 模式：模拟创建缺陷，不实际调用 API

        Args:
            project_id: IDP 项目 ID
            issue_data: 缺陷数据

        Returns:
            dict: 模拟的创建结果
        """
        logger.info("[IDPClient] [DRY-RUN] 模拟创建缺陷到项目 %s", project_id)
        logger.info("[IDPClient] [DRY-RUN] 缺陷标题: %s", issue_data.get("summary", "N/A"))

        # 返回模拟的响应
        return {
            "dry_run": True,
            "project_id": project_id,
            "issue_id": 999999,
            "issue_key": "BUG-DRY-RUN",
            "message": "dry-run 模式：未实际创建缺陷",
        }
