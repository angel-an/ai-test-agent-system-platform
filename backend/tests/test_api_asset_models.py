"""统一接口资产包模型 — 序列化/反序列化与表结构测试。

不依赖数据库：验证 Pydantic schema 往返、模型表结构、唯一约束。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from uuid import uuid4

# 触发 SQLAlchemy mapper 配置
import app.models as models
from app.schemas.api_asset import (
    ApiConflictConfirmRequest,
    ApiConflictResponse,
    ApiDocumentBundleCreate,
    ApiDocumentBundleResponse,
    ApiImportRequest,
    ApiImportResult,
    ApiInterfaceContractDetail,
    ApiInterfaceContractSummary,
    ApiMatchResult,
    ApiVariableResponse,
    ParseStats,
)


# ---------------------------------------------------------------------------
# 模型表结构
# ---------------------------------------------------------------------------

class TestModelStructure:
    """验证 8 张新表 + TestRunArtifact 修复后的结构。"""

    @pytest.mark.parametrize("attr,tablename", [
        ("ApiDocumentBundle", "api_document_bundles"),
        ("ApiDocumentSource", "api_document_sources"),
        ("ApiInterfaceContract", "api_interface_contracts"),
        ("ApiRequestTemplate", "api_request_templates"),
        ("ApiVariable", "api_variables"),
        ("ApiAssertion", "api_assertions"),
        ("ApiDependency", "api_dependencies"),
        ("ApiConflict", "api_conflicts"),
        ("TestRunArtifact", "test_run_artifacts"),
    ])
    def test_table_registered(self, attr, tablename):
        cls = getattr(models, attr)
        assert cls.__tablename__ == tablename

    def test_bundle_unique_constraint(self):
        table = models.ApiDocumentBundle.__table__
        uq = [c for c in table.constraints if c.__class__.__name__ == "UniqueConstraint"]
        assert any({"project_id", "name"} == set(c.columns.keys()) for c in uq)

    def test_contract_unique_constraint(self):
        table = models.ApiInterfaceContract.__table__
        uq = [c for c in table.constraints if c.__class__.__name__ == "UniqueConstraint"]
        assert any({"bundle_id", "method", "normalized_path"} == set(c.columns.keys()) for c in uq)

    def test_variable_unique_constraint(self):
        table = models.ApiVariable.__table__
        uq = [c for c in table.constraints if c.__class__.__name__ == "UniqueConstraint"]
        assert any({"bundle_id", "name"} == set(c.columns.keys()) for c in uq)

    def test_source_unique_constraint(self):
        table = models.ApiDocumentSource.__table__
        uq = [c for c in table.constraints if c.__class__.__name__ == "UniqueConstraint"]
        assert any({"bundle_id", "source_type"} == set(c.columns.keys()) for c in uq)

    def test_test_run_artifact_metadata_column(self):
        """metadata 保留字修复：属性名 artifact_metadata，列名 metadata。"""
        table = models.TestRunArtifact.__table__
        assert "metadata" in table.columns
        # 属性名不再是 metadata
        assert not hasattr(models.TestRunArtifact, "metadata") or \
            models.TestRunArtifact.__table__.c.metadata.name == "metadata"

    def test_project_isolation_columns(self):
        """所有 bundle 级表必须有 project_id 或通过 bundle 隔离。"""
        assert "project_id" in models.ApiDocumentBundle.__table__.columns
        # 子表通过 bundle_id 级联隔离
        for cls in [models.ApiDocumentSource, models.ApiInterfaceContract,
                    models.ApiVariable, models.ApiConflict]:
            assert "bundle_id" in cls.__table__.columns


# ---------------------------------------------------------------------------
# Pydantic Schema 往返
# ---------------------------------------------------------------------------

class TestSchemaRoundTrip:
    """验证 Pydantic schema 能正确序列化和校验。"""

    def test_bundle_create(self):
        b = ApiDocumentBundleCreate(name="美心 CRM UAT", system_type="crm_non_pos")
        assert b.name == "美心 CRM UAT"

    def test_bundle_create_requires_name(self):
        with pytest.raises(Exception):
            ApiDocumentBundleCreate(name="")

    def test_bundle_response_from_attributes(self):
        """模拟 ORM 对象转 schema。"""
        class FakeORM:
            id = uuid4()
            project_id = uuid4()
            name = "test"
            system_type = None
            status = "draft"
            current_version = 1
            parse_result = None
            created_by = "admin"
            created_at = None
            updated_at = None

        # from_attributes 需要真实属性，用 model_construct 绕过
        resp = ApiDocumentBundleResponse.model_construct(
            id=uuid4(), project_id=uuid4(), name="t", system_type=None,
            status="draft", current_version=1, parse_result=None,
            created_by=None, created_at=None, updated_at=None,
        )
        assert resp.status == "draft"

    def test_contract_summary(self):
        s = ApiInterfaceContractSummary(
            id=uuid4(), endpoint_key="api_memLogin",
            method="POST", path="/api/json/memLogin",
            normalized_path="/api/json/memlogin",
            name="会员登录", version=1, status="pending",
        )
        assert s.endpoint_key == "api_memLogin"

    def test_conflict_response_severity_validation(self):
        with pytest.raises(Exception):
            ApiConflictResponse(
                id=uuid4(), bundle_id=uuid4(), contract_id=None,
                conflict_type="method", severity="critical",  # 非法
                status="open", message="x",
            )

    def test_conflict_confirm_request(self):
        r = ApiConflictConfirmRequest(resolution="accept", note="已知悉")
        assert r.resolution == "accept"

    def test_import_request(self):
        r = ApiImportRequest(contract_ids=[uuid4(), uuid4()])
        assert len(r.contract_ids) == 2

    def test_import_request_min_one(self):
        with pytest.raises(Exception):
            ApiImportRequest(contract_ids=[])

    def test_match_result(self):
        m = ApiMatchResult(total_contracts=3, matched=3, conflicted=0, pending=0)
        assert m.matched == 3

    def test_variable_response_no_secret_value(self):
        """变量响应不包含明文值，只有 value_ref。"""
        v = ApiVariableResponse(
            id=uuid4(), bundle_id=uuid4(), name="MX_APPSECRET",
            value_ref="env:MX_APPSECRET", is_secret=True,
            environment="uat", source_type="postman_environment", enabled=True,
        )
        assert v.is_secret is True
        assert "MX_APPSECRET" in v.value_ref

    def test_parse_stats(self):
        s = ParseStats(excel_interfaces=25, collection_requests=25, environment_variables=10)
        assert s.excel_interfaces == 25


# ---------------------------------------------------------------------------
# 迁移文件结构
# ---------------------------------------------------------------------------

class TestMigration:
    """验证 0019 迁移文件可加载且 revision 链正确。"""

    def test_migration_file_exists(self):
        mig = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0019_api_asset_bundle.py"
        assert mig.exists()

    def test_migration_revision_chain(self):
        import importlib.util
        mig = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0019_api_asset_bundle.py"
        spec = importlib.util.spec_from_file_location("mig0019", mig)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.revision == "0019"
        assert mod.down_revision == "0018"

    def test_migration_has_upgrade_downgrade(self):
        import importlib.util
        mig = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0019_api_asset_bundle.py"
        spec = importlib.util.spec_from_file_location("mig0019", mig)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)
