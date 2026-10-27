"""API run 编号去重 + 全局唯一约束（2026-10-27）

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-27

背景：``APITestRunRepository.get_next_identifier`` 曾按 ``api_test_id`` 计数，
同一项目下 N 个 api_test 每天各自从 001 起号 ⇒ 实测 **33 组重号**
（如 ``ATR-20260824-003`` 被 3 条不同记录共用）。看到编号无法定位到具体哪一次执行，
编号本身失去意义。代码侧已改为**全局按日期**递增（与 web 侧 ``TR-`` 口径一致），
本迁移负责存量与约束。

两步顺序不能反：

1. **先给重复组里非最早的那些重编号**（保留最早一条的原文）。新编号取该日期前缀
   当天已用的最大序号 +1，遇到占用继续往后找 —— 保证不与任何现有编号相撞；
2. **再建唯一索引** —— 存量没清干净直接建索引会失败。

为什么唯一性放在 ``identifier`` 全局而非 ``(project_id, identifier)``：
编号格式 ``ATR-YYYYMMDD-NNN`` 里没有项目段，只有全局唯一才能保证
"看到编号就知道是哪一次执行"。

为什么建成**部分索引只覆盖 ``ATR-%``**：实测该列还混着 ``SR-EP-<endpoint_id>``
（27 条 / 7 个 distinct）—— 那是"同一脚本资产的执行"，编号按 endpoint 固定复用，
重复是设计意图，不是缺陷。全局唯一索引会直接建不起来（首次尝试就撞在
``SR-EP-ed6eceb4-…`` 上）。`SR-EP` 编号无法定位单次执行是另一个独立问题，不在本次范围。

降级：只删索引，**不还原编号**（重编号不可逆，回退会把重复重新引进来）。
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"

_INDEX_NAME = "uq_api_test_runs_identifier_atr"

# 重编号：只处理严格匹配 ATR-YYYYMMDD-NNN 的值，异构值（历史上有过 SR-EP-*）一律跳过。
_REINDEX_SQL = """
DO $$
DECLARE
    rec RECORD;
    prefix TEXT;
    next_num INT;
    candidate TEXT;
BEGIN
    FOR rec IN
        SELECT id, identifier FROM (
            SELECT id,
                   identifier,
                   row_number() OVER (
                       PARTITION BY identifier ORDER BY created_at, id
                   ) AS rn
            FROM api_test_runs
            WHERE identifier ~ '^ATR-[0-9]{8}-[0-9]+$'
        ) ranked
        WHERE ranked.rn > 1
        ORDER BY identifier, ranked.rn
    LOOP
        prefix := substring(rec.identifier from '^(ATR-[0-9]{8}-)');
        SELECT COALESCE(MAX(CAST(substring(identifier from '([0-9]+)$') AS INT)), 0) + 1
          INTO next_num
          FROM api_test_runs
         WHERE identifier LIKE prefix || '%';
        LOOP
            candidate := prefix || lpad(next_num::text, 3, '0');
            EXIT WHEN NOT EXISTS (
                SELECT 1 FROM api_test_runs WHERE identifier = candidate
            );
            next_num := next_num + 1;
        END LOOP;
        UPDATE api_test_runs SET identifier = candidate WHERE id = rec.id;
    END LOOP;
END $$;
"""


def upgrade() -> None:
    op.execute(_REINDEX_SQL)
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX_NAME} "
        "ON api_test_runs (identifier) WHERE identifier LIKE 'ATR-%'"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX_NAME}")
