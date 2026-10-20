"""PR-9 积分商城只读查询测试骨架。

安全跳过模式：无环境变量时自动跳过，不报错。
"""

from __future__ import annotations

import os

import pytest

# 门控：无环境变量时安全跳过
pytestmark = pytest.mark.skipif(
    os.getenv("PR9_MEMBER_TOKEN") is None,
    reason="PR-9 只读试点：需配置 PR9_MEMBER_TOKEN 等环境变量才执行",
)


class TestPR9ReadonlyQuery:
    """PR-9 积分商城只读查询测试。"""

    @pytest.fixture(scope="session")
    def pr9_base_url(self) -> str:
        return os.getenv("PR9_API_BASE_URL", "https://imarketing-uat.xysjg.com")

    @pytest.fixture(scope="session")
    def pr9_member_id(self) -> str:
        return os.getenv("PR9_MEMBER_ID", "")

    @pytest.fixture(scope="session")
    def pr9_headers(self) -> dict[str, str]:
        token = os.getenv("PR9_MEMBER_TOKEN", "")
        return {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        }

    def test_p1_product_list_query(self, pr9_base_url, pr9_member_id, pr9_headers):
        """商品列表查询：获取当前等级可见且活动周期内的商品。"""
        # TODO: 确认真实接口路径后实现
        pytest.skip("接口路径待确认：/api/.../products")

    def test_p1_preview_status_query(self, pr9_base_url, pr9_member_id, pr9_headers):
        """预热状态查询：校验倒计时文案和按钮态。"""
        # TODO: 确认真实接口路径后实现
        pytest.skip("接口路径待确认：/api/.../products/{id}/preview")

    def test_p1_transaction_record_query(self, pr9_base_url, pr9_member_id, pr9_headers):
        """交易记录查询：按时间倒序展示兑换记录。"""
        # TODO: 确认真实接口路径后实现
        pytest.skip("接口路径待确认：/api/.../member/{id}/transactions")
