"""Publish source-based single endpoint artifacts and actual Playwright results."""
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from publish_coupon_template_api_test_artifacts import configure_database

async def main():
    configure_database()
    sys.path.insert(0, str(ROOT / 'backend'))
    from sqlalchemy import select
    from app.config.database import async_session_factory, engine
    from app.config.settings import settings
    from app.models.project import Project
    from app.models.api_endpoint import APIEndpoint
    from app.models.api_test import APITest, APITestRun, APITestResult
    from app.models.attachment import Attachment, AttachmentEntityType
    from app.repositories.api_test_repo import APITestRepository
    from app.schemas.enums import TestResultStatus
    from app.config.minio_client import MinIOClient

    workspace = ROOT / 'backend/workspace/api'
    out = workspace / 'reports/maxims-hk-single'
    report = json.loads((out / 'playwright-result.json').read_text(encoding='utf-8'))
    rows = []
    def walk(s):
        for spec in s.get('specs', []):
            for t in spec['tests']:
                result = t['results'][-1]
                rows.append({'name': spec['title'], 'status': result['status'], 'annotations': t.get('annotations', [])})
        for child in s.get('suites', []): walk(child)
    walk(report)
    assert len(rows) == 14 and not report.get('errors'), 'Invalid runner evidence'
    counts = {s: sum(r['status'] == s for r in rows) for s in ['passed','failed','skipped','timedOut']}
    blocked = counts['skipped'] > 0
    summary = {'project':'PR-9', 'endpoint':'api_checkMobileDuplicate', 'status':'blocked' if blocked else 'completed', 'total':len(rows), **counts, 'cases':rows}
    (out / 'execution-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (out / 'test-cases.json').write_text(json.dumps({'test_cases':rows, 'oracle_source':'requirement-and-plan.md'},ensure_ascii=False,indent=2),encoding='utf-8')
    (out / 'report.md').write_text(f"# 美心-HK 单接口执行报告\n\n接口：POST api_checkMobileDuplicate\n\n执行状态：{summary['status']}\n\n总计14，通过{counts['passed']}，失败{counts['failed'] + counts['timedOut']}，跳过{counts['skipped']}。\n\n" + ('缺少测试URL、appid/appsecret，未发出业务请求。补齐已存在/不存在手机号后重跑。此报告不证明接口通过。' if blocked else '结果来自Playwright真实执行。'),encoding='utf-8')
    files = [('API_TEST_PLAN',out/'requirement-and-plan.md'),('API_TEST_CASE',out/'test-cases.json'),('API_TEST_SCRIPT',workspace/'tests/maxims-hk-check-mobile.spec.ts'),('API_TEST_RESULT',out/'execution-summary.json'),('API_TEST_REPORT',out/'report.md')]
    async with async_session_factory() as db:
        p = (await db.execute(select(Project).where(Project.identifier=='PR-9'))).scalar_one()
        assert p.name == '美心项目-香港HK'
        path='/api/json/api_checkMobileDuplicate'
        endpoint = (await db.execute(select(APIEndpoint).where(APIEndpoint.project_id==p.id,APIEndpoint.path==path))).scalar_one_or_none()
        if endpoint is None:
            endpoint=APIEndpoint(project_id=p.id,display_name='POST api_checkMobileDuplicate',path=path,method='POST',summary='COP 手机号查重',description='逻辑路径来自旧规格；真实网关URL待配置。详见测试计划。',tag_group='三方接口-COP',tags=['Non POS','单接口'],parameters=[],responses={'200':{'description':'Code=0; Data.Existed=MX1 or null'}},custom_config={'url_env':'MAXIMS_HK_API_URL','source_sheet':'COP api_checkMobileDuplicat'})
            db.add(endpoint)
            await db.flush()
        prefix=f'api-tests/PR-9/endpoints/{endpoint.id}/maxims-single'
        test=(await db.execute(select(APITest).where(APITest.project_id==p.id,APITest.name=='COP 手机号查重单接口测试'))).scalar_one_or_none()
        if test is None:
            test=APITest(project_id=p.id,identifier=f'AT-MXHK-{uuid4().hex[:8].upper()}',name='COP 手机号查重单接口测试',script_path=f'{prefix}/maxims-hk-check-mobile.spec.ts',script_format='playwright',script_language='typescript',total_endpoints=1,total_scenarios=14,generated_by_agent='codex',test_config={'credential_source':'runtime_environment_only'},generation_params={'source':'YX CRM API Spec For Non POS (1).xlsx'})
            db.add(test)
            await db.flush()
        run=APITestRun(project_id=p.id,api_test_id=test.id,identifier=f'ATR-MXHK-{uuid4().hex[:12]}',status='failed' if blocked else 'completed',total_tests=14,passed_tests=counts['passed'],failed_tests=counts['failed']+counts['timedOut'],skipped_tests=counts['skipped'],execution_config={'business_status':summary['status'],'runner':'Playwright','config':'maxims-hk.config.ts'},error_message='BLOCKED: missing MAXIMS_HK_API_URL, MAXIMS_HK_APP_ID, MAXIMS_HK_APP_SECRET; no HTTP requests' if blocked else None)
        db.add(run)
        await db.flush()
        for r in rows:
            db.add(APITestResult(test_run_id=run.id,api_test_id=test.id,scenario_name=r['name'],endpoint=path,method='POST',status=TestResultStatus.SKIPPED if r['status']=='skipped' else TestResultStatus.PASSED if r['status']=='passed' else TestResultStatus.FAILED,error_message='; '.join(a.get('description','') for a in r['annotations']) or None))
        endpoint.api_test_ids=list(set((endpoint.api_test_ids or [])+[str(test.id)]))
        endpoint.total_test_cases=14
        endpoint.total_test_runs=(endpoint.total_test_runs or 0)+1
        endpoint.last_run_status='blocked' if blocked else 'completed'
        for kind, file in files:
            data=file.read_bytes()
            obj=f'{prefix}/{file.name}'
            mime='application/json' if file.suffix=='.json' else 'text/markdown' if file.suffix=='.md' else 'text/plain'
            # Framework supports local artifact backup for unavailable object storage.
            backup=Path(settings.api_workspace_root).resolve()/'artifacts_backup'/obj
            backup.parent.mkdir(parents=True,exist_ok=True)
            backup.write_bytes(data)
            a=(await db.execute(select(Attachment).where(Attachment.object_name==obj))).scalar_one_or_none()
            if a is None:
                a=Attachment(entity_type=AttachmentEntityType[kind],entity_id=endpoint.id,project_id=p.id,file_name=file.name,file_size=len(data),content_type=mime,object_name=obj,created_by='codex')
                db.add(a)
            else:
                a.file_size=len(data)
            assert MinIOClient._try_local_backup(obj)==data
        await db.commit()
        saved=(await db.execute(select(Attachment).where(Attachment.entity_id==endpoint.id))).scalars().all()
        result={'project':p.identifier,'endpoint_id':str(endpoint.id),'api_test_id':str(test.id),'run_id':str(run.id),'attachments':len(saved),'status':summary['status']}
        (out/'platform-registration.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))
    await engine.dispose()

if __name__=='__main__': asyncio.run(main())
