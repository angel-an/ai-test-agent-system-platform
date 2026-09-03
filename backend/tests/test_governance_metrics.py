"""rev57：执行治理只读指标测试。

SQLite 造数据验证 executable_rate / heal_adoption_rate / human_oracle_rate
与 flaky_rate=None（数据不足不估算）。
"""

from uuid import uuid4

import pytest
from sqlalchemy import text as _text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
async def metrics_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.execute(_text(
            "CREATE TABLE web_sub_functions ("
            "id CHAR(32) PRIMARY KEY, project_id CHAR(32), function_id CHAR(32), "
            "identifier VARCHAR(50), display_name VARCHAR(200), name VARCHAR(200), "
            "expected_results TEXT, compiled_assertions TEXT, assertion_mode VARCHAR(20), "
            "created_at DATETIME)"
        ))
        await conn.execute(_text(
            "CREATE TABLE attachments ("
            "id CHAR(32) PRIMARY KEY, entity_type VARCHAR(50), entity_id CHAR(32), "
            "project_id CHAR(32), object_name VARCHAR(500), created_at DATETIME)"
        ))
        await conn.execute(_text(
            "CREATE TABLE web_script_registry ("
            "id CHAR(32) PRIMARY KEY, project_identifier VARCHAR(100), attachment_id CHAR(32), "
            "script_hash VARCHAR(64), version_status VARCHAR(20), script_language VARCHAR(32), "
            "script_format VARCHAR(32), created_by VARCHAR(64), created_at DATETIME)"
        ))
        await conn.execute(_text(
            "CREATE TABLE web_script_reviews ("
            "id CHAR(32) PRIMARY KEY, sub_function_id CHAR(32), status VARCHAR(20), "
            "error_summary TEXT, retry_count INTEGER, version_no INTEGER, "
            "proposed_attachment_id CHAR(32), created_at DATETIME)"
        ))
        await conn.execute(_text(
            "CREATE TABLE web_test_runs ("
            "id CHAR(32) PRIMARY KEY, project_id CHAR(32), web_test_id CHAR(32), "
            "identifier VARCHAR(50), status VARCHAR(20), created_at DATETIME)"
        ))
    yield engine
    await engine.dispose()


def _factory(engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed(engine):
    """4 个子功能：2 个有 effective 脚本；expected_results 混合编译/人工。"""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    async with AsyncSession(engine) as s:
        sf_ids = [uuid4() for _ in range(4)]
        er = [
            ["订单状态为已发货", "页面包含配送中"],   # compiled
            ["成功"],                             # human_oracle
            ["余额=100", "结果正常"],              # mixed
            None,                                # 无 expected
        ]
        for i, sf in enumerate(sf_ids):
            await s.execute(_text(
                "INSERT INTO web_sub_functions (id, project_id, function_id, identifier, "
                "display_name, name, expected_results, created_at) "
                "VALUES (:id, :p, :f, :idf, :dn, :n, :er, :ts)"
            ), {"id": sf.hex, "p": uuid4().hex, "f": uuid4().hex,
                "idf": f"WSF-{i}", "dn": f"sf{i}", "n": f"sf{i}",
                "er": _json(er[i]) if er[i] is not None else None, "ts": now})
        # 前 2 个子功能有 effective 脚本附件
        for sf in sf_ids[:2]:
            att = uuid4()
            await s.execute(_text(
                "INSERT INTO attachments (id, entity_type, entity_id, project_id, "
                "object_name, created_at) VALUES (:id, 'WEB_TEST_SCRIPT', :e, :p, :o, :ts)"
            ), {"id": att.hex, "e": sf.hex, "p": uuid4().hex, "o": f"obj/{sf.hex}.py", "ts": now})
            await s.execute(_text(
                "INSERT INTO web_script_registry (id, project_identifier, attachment_id, "
                "script_hash, version_status, created_at) "
                "VALUES (:id, 'P', :a, 'h', 'effective', :ts)"
            ), {"id": uuid4().hex, "a": att.hex, "ts": now})
        # reviews：3 adopted（passed）+ 1 rejected
        for i, st in enumerate(["passed", "passed", "passed", "rejected"]):
            await s.execute(_text(
                "INSERT INTO web_script_reviews (id, sub_function_id, status, retry_count, "
                "version_no, created_at) VALUES (:id, :sf, :st, 0, 1, :ts)"
            ), {"id": uuid4().hex, "sf": uuid4().hex, "st": st, "ts": now})
        # web_test_runs（flaky 历史）：组 A 两次 completed（不 flaky）；
        # 组 B completed+failed（flaky）→ 2 组候选 1 组 flaky
        wt_a, wt_b = uuid4(), uuid4()
        for wt_grp, statuses in [(wt_a, ["completed", "completed"]),
                                 (wt_b, ["completed", "failed"])]:
            for st in statuses:
                await s.execute(_text(
                    "INSERT INTO web_test_runs (id, project_id, web_test_id, identifier, "
                    "status, created_at) VALUES (:id, :p, :wt, :idf, :st, :ts)"
                ), {"id": uuid4().hex, "p": uuid4().hex, "wt": wt_grp.hex,
                    "idf": f"WTR-{wt_grp.hex[:4]}", "st": st, "ts": now})
        await s.commit()
    return sf_ids


def _json(v):
    import json

    return json.dumps(v, ensure_ascii=False)


class TestGovernanceMetrics:
    async def test_metrics_computed(self, metrics_engine):
        from sqlalchemy.ext.asyncio import AsyncSession

        await _seed(metrics_engine)
        from app.services.execution.metrics import compute_execution_governance_metrics

        m = await compute_execution_governance_metrics(_factory(metrics_engine))
        # executable：2/4 = 0.5
        assert m["executable_rate"] == 0.5
        assert m["executable"]["sub_functions_with_effective"] == 2
        assert m["executable"]["total_sub_functions"] == 4
        # heal adoption：3/(3+1) = 0.75
        assert m["heal_adoption_rate"] == 0.75
        assert m["heal"]["adopted"] == 3
        assert m["heal"]["rejected"] == 1
        # human_oracle：expected 条数 = 1(compiled) +1(ho) +2(mixed 中 1 compiled+1 ho)=
        #   items: sf0:1+1=2, sf1:1, sf2:1+1=2 → total=5, human=1+1=2 → 0.4
        assert m["human_oracle_rate"] == 0.4, m
        assert m["human_oracle"]["total_expected_items"] == 5
        assert m["human_oracle"]["human_oracle_items"] == 2
        # flaky（rev58 历史 run）：2 组候选（A 一致 / B 冲突）→ 0.5
        assert m["flaky_rate"] == 0.5, m
        assert m["flaky"]["sampled_groups"] == 2
        assert m["flaky"]["flaky_groups"] == 1

    async def test_metrics_empty_db(self, metrics_engine):
        from app.services.execution.metrics import compute_execution_governance_metrics

        m = await compute_execution_governance_metrics(_factory(metrics_engine))
        assert m["executable_rate"] == 0.0
        assert m["heal_adoption_rate"] == 0.0
        assert m["human_oracle_rate"] == 0.0
        assert m["flaky_rate"] is None


class TestGovernanceMetricsApi:
    async def test_sample_flakiness_route(self, monkeypatch):
        from types import SimpleNamespace

        import app.api.v2.governance_metrics as api_mod

        async def _fake_sample(*args, **kwargs):
            return {"sub_function_id": kwargs.get("sub_function_id"), "runs": 3, "flaky": True}

        monkeypatch.setattr(
            "app.services.execution.metrics.sample_script_flakiness",
            _fake_sample,
        )
        resp = await api_mod.sample_flakiness(
            api_mod.SampleFlakinessRequest(
                sub_function_id="11111111-1111-1111-1111-111111111111",
                runs=3,
            ),
            current_user=SimpleNamespace(username="admin", id="u1"),
        )
        assert resp["flaky"] is True
        assert resp["runs"] == 3
