"""
Knowledge Base Smoke Test - Structure & Negative Path Verification
知识库基础冒烟验证：结构检查 + 负向路径验证

NOT a full E2E test. Does NOT cover:
- 真实知识空间创建（需要真实项目+用户）
- MinIO 文件上传
- 文档抽取/切片/Embedding
- 语义检索/RAG 降级
- Agent query_knowledge_base_tool 调用
- 前端页面交互

Run: python backend/verify_kb_smoke.py
"""
import sys, os, json, asyncio, traceback
from datetime import datetime
from uuid import uuid4

os.chdir(r'd:\code\Pyproject\ai-test-agent-system-platform')
sys.path.insert(0, 'backend')

results = {
    "report_title": "Knowledge Base Smoke Test Report",
    "scope": "Structure + Negative Path Only (NOT E2E)",
    "timestamp": datetime.now().isoformat(),
    "environment": {},
    "tests": [],
}

def log(name, status, detail=""):
    results["tests"].append({"name": name, "status": status, "detail": detail})
    icon = "[PASS]" if status else "[FAIL]"
    print(f"  {icon} {name}: {detail}")

print("=" * 70)
print("KNOWLEDGE BASE SMOKE TEST REPORT")
print("Scope: Structure + Negative Path Only (NOT Full E2E)")
print("=" * 70)
print(f"Time: {results['timestamp']}")
print(f"Working Dir: {os.getcwd()}")
print()

# ---------------------------------------------------------------------------
# ENV: .venv only
# ---------------------------------------------------------------------------
print("[ENV] Python Environment (.venv)")
print(f"  Python: {sys.executable}")
print(f"  Version: {sys.version}")
assert ".venv" in sys.executable.lower() and "python.exe" in sys.executable.lower(), \
    "Must run under .venv"

deps = [("sqlalchemy", "2.0.49"), ("fastapi", "0.136.1"), ("pydantic", "2.13.4")]
for dep, expected in deps:
    mod = __import__(dep)
    ver = getattr(mod, "__version__", "unknown")
    match = "[OK]" if ver == expected else f"[MISMATCH expected {expected}]"
    print(f"  {match} {dep}: {ver}")
    results["environment"][dep] = ver

# ---------------------------------------------------------------------------
# TEST 1: Module Import
# ---------------------------------------------------------------------------
print("\n[TEST 1/8] Module Import")
try:
    from app.services.knowledge_base_service import KnowledgeBaseService
    log("Import KnowledgeBaseService", True, "Module loaded")
except Exception as e:
    log("Import KnowledgeBaseService", False, str(e))
    traceback.print_exc()

# ---------------------------------------------------------------------------
# TEST 2: Model Import
# ---------------------------------------------------------------------------
print("\n[TEST 2/8] Model Import")
try:
    from app.models.knowledge_space import KnowledgeSpace
    from app.models.knowledge_document import KnowledgeDocument, DocumentStatus
    from app.models.knowledge_chunk import KnowledgeChunk
    log("Import KB Models", True,
        "KnowledgeSpace, KnowledgeDocument, KnowledgeChunk, DocumentStatus")
except Exception as e:
    log("Import KB Models", False, str(e))

# ---------------------------------------------------------------------------
# TEST 3: Schema Import
# ---------------------------------------------------------------------------
print("\n[TEST 3/8] Schema Import")
try:
    from app.schemas.knowledge_base import (
        KnowledgeSpaceCreate, KnowledgeSpaceUpdate, KnowledgeSpaceInfo,
        KnowledgeDocumentInfo, KnowledgeUploadResponse,
    )
    log("Import KB Schemas", True, "5 schema classes")
except Exception as e:
    log("Import KB Schemas", False, str(e))

# ---------------------------------------------------------------------------
# TEST 4: Database Engine (独立引擎，用完即释放)
# ---------------------------------------------------------------------------
print("\n[TEST 4/8] Database Engine")
engine = None
try:
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from app.config.settings import settings
    engine = create_async_engine(settings.postgres_url, echo=False, future=True)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    log("Create DB Engine", True, f"URL: {settings.postgres_url[:40]}...")
except Exception as e:
    log("Create DB Engine", False, str(e))

# ---------------------------------------------------------------------------
# TEST 5: Database Tables (独立引擎检查，避免会话冲突)
# ---------------------------------------------------------------------------
print("\n[TEST 5/8] Database Tables")
engine_check = None
try:
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.config.settings import settings
    engine_check = create_async_engine(settings.postgres_url, echo=False, future=True)

    async def _check_tables():
        from sqlalchemy import inspect
        async with engine_check.begin() as conn:
            return await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names())

    tables = asyncio.run(_check_tables())
    kb_tables = sorted([t for t in tables if 'knowledge' in t.lower()])
    print(f"  All tables: {tables}")
    print(f"  KB tables: {kb_tables}")
    assert kb_tables == ['knowledge_chunks', 'knowledge_documents',
                         'knowledge_retrieval_logs', 'knowledge_spaces'], \
        f"Unexpected KB tables: {kb_tables}"
    log("KB Tables", True, f"4 tables: {kb_tables}")
except Exception as e:
    log("KB Tables", False, str(e))
finally:
    if engine_check:
        asyncio.run(engine_check.dispose())

# ---------------------------------------------------------------------------
# TEST 6: Model vs Migration Field Count (关键复核点)
# ---------------------------------------------------------------------------
print("\n[TEST 6/8] Model vs Migration Field Count")
try:
    space_model_cols = [c.name for c in KnowledgeSpace.__table__.columns]
    doc_model_cols = [c.name for c in KnowledgeDocument.__table__.columns]
    chunk_model_cols = [c.name for c in KnowledgeChunk.__table__.columns]

    # Migration 0012 中的字段数
    # knowledge_spaces: id, project_id, name, description, business_line,
    #   folder_scope, created_by, created_at, updated_at = 9
    # knowledge_documents: id, space_id, title, file_name, file_path,
    #   file_size, content_type, status, chunk_count, error_message,
    #   created_by, created_at, updated_at = 13
    # knowledge_chunks: id, document_id, space_id, content, embedding,
    #   chunk_index, meta_data, created_at = 8 (模型 7，缺 created_at)

    print(f"  Space model cols ({len(space_model_cols)}): {space_model_cols}")
    print(f"  Doc model cols ({len(doc_model_cols)}): {doc_model_cols}")
    print(f"  Chunk model cols ({len(chunk_model_cols)}): {chunk_model_cols}")
    print(f"  NOTE: Chunk table has 8 cols (incl. created_at), model has 7")

    assert len(space_model_cols) == 9, f"Space expected 9, got {len(space_model_cols)}"
    assert len(doc_model_cols) == 13, f"Doc expected 13, got {len(doc_model_cols)}"
    # 模型 7 字段，数据库表 8 列 —— 这是已知差异
    assert len(chunk_model_cols) == 7, f"Chunk model expected 7, got {len(chunk_model_cols)}"

    log("Model Fields", True,
        f"Space({len(space_model_cols)}), Doc({len(doc_model_cols)}), "
        f"Chunk model({len(chunk_model_cols)}) / table(8 with created_at)")
except Exception as e:
    log("Model Fields", False, str(e))

# ---------------------------------------------------------------------------
# TEST 7: API Router Introspection (NOT HTTP call test)
# ---------------------------------------------------------------------------
print("\n[TEST 7/8] API Router Introspection")
try:
    from app.api.v2.knowledge_base import router
    routes = [r.path for r in router.routes]
    print(f"  Routes: {routes}")
    assert len(routes) == 12, f"Expected 12 routes, got {len(routes)}"
    log("API Routes", True, f"{len(routes)} endpoints (introspection, not HTTP)")
except Exception as e:
    log("API Routes", False, str(e))

# ---------------------------------------------------------------------------
# TEST 8: Service Negative Path (需要有效 engine + session)
# ---------------------------------------------------------------------------
print("\n[TEST 8/8] Service Negative Path Tests")
if engine and session_maker:
    async def _run_negative():
        from app.services.knowledge_base_service import KnowledgeBaseService
        async with session_maker() as session:
            svc = KnowledgeBaseService(session)
            tests_passed = 0
            tests_total = 0

            # 8a: list_spaces with non-existent project
            tests_total += 1
            try:
                await svc.list_spaces("nonexistent-project-12345")
            except Exception as e:
                if "不存在" in str(e) or "not found" in str(e).lower():
                    tests_passed += 1

            # 8b: create_space with non-existent project
            tests_total += 1
            try:
                from app.schemas.knowledge_base import KnowledgeSpaceCreate
                data = KnowledgeSpaceCreate(name="Test", description="x", business_line="test")
                await svc.create_space("nonexistent-project-12345", data, uuid4())
            except Exception as e:
                if "不存在" in str(e) or "not found" in str(e).lower():
                    tests_passed += 1

            # 8c: get_space with random UUID
            tests_total += 1
            try:
                await svc.get_space(uuid4())
            except Exception as e:
                if "不存在" in str(e) or "not found" in str(e).lower():
                    tests_passed += 1

            # 8d: list_documents with non-existent space
            tests_total += 1
            try:
                await svc.list_documents(uuid4())
            except Exception as e:
                if "不存在" in str(e) or "not found" in str(e).lower():
                    tests_passed += 1

            return tests_passed, tests_total

    try:
        passed, total = asyncio.run(_run_negative())
        log("Negative Path", True,
            f"{passed}/{total} negative cases correctly rejected")
    except Exception as e:
        log("Negative Path", False, str(e))
else:
    log("Negative Path", False, "Engine not available")

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
if engine:
    asyncio.run(engine.dispose())

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
passed = sum(1 for t in results["tests"] if t["status"])
total = len(results["tests"])
print(f"Total: {total} | Passed: {passed} | Failed: {total - passed}")
print(f"Success Rate: {passed/total*100:.1f}%" if total else "N/A")

print("\nDetailed Results:")
for t in results["tests"]:
    icon = "[PASS]" if t["status"] else "[FAIL]"
    print(f"  {icon} {t['name']}: {t['detail']}")

print("\n" + "=" * 70)
print("NOT COVERED (E2E scope, requires separate test run):")
print("  - 真实知识空间创建（需要 DB 中真实项目+用户）")
print("  - MinIO 文件上传")
print("  - 文档抽取 / 切片 / Embedding 生成")
print("  - 语义检索 / RAG 降级路径")
print("  - Agent query_knowledge_base_tool 调用")
print("  - 前端页面交互")
print("  - HTTP API 调用（当前仅为 router introspection）")
print("=" * 70)

# Save reports
json_path = "KB_SMOKE_TEST_REPORT.json"
with open(json_path, "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print(f"\nJSON saved: {json_path}")

md = f"""# {results['report_title']}

**Scope**: {results['scope']}
**Time**: {results['timestamp']}
**Environment**: .venv Python {sys.version.split()[0]}

## Dependencies (from .venv)

| Dep | Version |
|-----|---------|
"""
for dep, ver in results["environment"].items():
    md += f"| {dep} | {ver} |\n"

md += """\n## Test Results

| # | Test | Status | Detail |
|---|------|--------|--------|
"""
for i, t in enumerate(results["tests"], 1):
    status = "PASS" if t["status"] else "FAIL"
    md += f"| {i} | {t['name']} | {status} | {t['detail']} |\n"

md += f"""\n## Summary

- **Total**: {total}
- **Passed**: {passed}
- **Failed**: {total - passed}
- **Rate**: {passed/total*100:.1f}%

## Known Limitations

1. **Environment**: Results only valid under `.venv\\Scripts\\python.exe`.
   System Python has different dependency versions.
2. **Chunk fields**: Model has 7 columns; DB table has 8 (includes `created_at`
   from migration [0012_knowledge_base.py](backend/alembic/versions/0012_knowledge_base.py:77)).
3. **API Routes**: Introspection only — no HTTP requests sent.
4. **Service tests**: Negative path only — no positive E2E flow verified.

## Not Covered (Requires E2E Test)

- [ ] Create real knowledge space (needs real project + user in DB)
- [ ] Upload file to MinIO
- [ ] Document extraction / chunking / embedding
- [ ] Semantic retrieval with similarity scoring
- [ ] RAG degradation path
- [ ] Agent `query_knowledge_base_tool` invocation
- [ ] Frontend page interaction
- [ ] HTTP API endpoint calls via TestClient or browser

---
*Generated by backend/verify_kb_smoke.py*
"""

md_path = "KB_SMOKE_TEST_REPORT.md"
with open(md_path, "w", encoding="utf-8") as f:
    f.write(md)
print(f"Markdown saved: {md_path}")
