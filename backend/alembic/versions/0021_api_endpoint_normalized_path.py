"""api_endpoints 增加 normalized_path（统一导入幂等键）。

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-14

背景：
统一接口资产包导入（parse-bundle → confirm）需要以
``项目 + method + normalized_path`` 作为幂等键，保证重复确认不会重复创建端点，
并保证同一接口在多次导入 / 多份文档之间稳定复用同一个 APIEndpoint。

本迁移：
1. 新增 ``api_endpoints.normalized_path``（可空，兼容既有数据）；
2. 回填既有行：把 path 按与导入侧一致的规则归一化（去协议/host、去尾斜杠、小写、去 query）；
3. 回填后若仍存在 (project_id, method, normalized_path) 重复，则将重复行的
   normalized_path 置空（不破坏数据，仅使其不参与幂等匹配），避免唯一约束创建失败；
4. 创建索引与唯一约束。

降级：删除约束、索引与列。
"""

from typing import Sequence, Union

import re

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID_RE = re.compile(r"^[0-9a-fA-F-]{8,}$")


def _normalize(path: str | None) -> str:
    """与 app.services.api_asset_matcher.normalize_path 保持一致的归一化。"""
    if not path:
        return ""
    s = str(path).strip()
    m = re.match(r"https?://([^/]+)(/.*)?", s, re.IGNORECASE)
    if m:
        s = m.group(2) or "/"
    s = re.sub(r"\{\{(\w+)\}\}", "", s)
    if not s.startswith("/"):
        idx = s.find("/api/")
        s = s[idx:] if idx >= 0 else "/" + s
    s = s.split("?")[0]
    if len(s) > 1:
        s = s.rstrip("/")
    return s.lower()


def upgrade() -> None:
    op.add_column(
        "api_endpoints",
        sa.Column(
            "normalized_path",
            sa.String(length=2048),
            nullable=True,
            comment="归一化路径（统一导入幂等键的一部分）",
        ),
    )

    conn = op.get_bind()

    # 1) 回填 normalized_path
    rows = conn.execute(
        sa.text("SELECT id, path FROM api_endpoints WHERE path IS NOT NULL")
    ).fetchall()
    for row in rows:
        normalized = _normalize(row[1])
        if normalized:
            conn.execute(
                sa.text("UPDATE api_endpoints SET normalized_path = :np WHERE id = :id"),
                {"np": normalized, "id": row[0]},
            )

    # 2) 处理回填后仍重复的行：保留最早创建的一条，其余置空
    dup_rows = conn.execute(
        sa.text(
            """
            SELECT project_id, method, normalized_path, array_agg(id ORDER BY created_at, id) AS ids
            FROM api_endpoints
            WHERE normalized_path IS NOT NULL
            GROUP BY project_id, method, normalized_path
            HAVING count(*) > 1
            """
        )
    ).fetchall()
    for dup in dup_rows:
        ids = list(dup[3])
        for extra_id in ids[1:]:
            conn.execute(
                sa.text("UPDATE api_endpoints SET normalized_path = NULL WHERE id = :id"),
                {"id": extra_id},
            )

    op.create_index(
        "ix_api_endpoints_normalized_path", "api_endpoints", ["normalized_path"]
    )
    op.create_unique_constraint(
        "uq_endpoint_project_method_normpath",
        "api_endpoints",
        ["project_id", "method", "normalized_path"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_endpoint_project_method_normpath", "api_endpoints", type_="unique"
    )
    op.drop_index("ix_api_endpoints_normalized_path", table_name="api_endpoints")
    op.drop_column("api_endpoints", "normalized_path")
