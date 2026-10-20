import asyncio
import json
import asyncpg
from dotenv import dotenv_values

async def main():
    config = dotenv_values('.env')
    db = await asyncpg.connect((config.get('POSTGRES_URI') or config.get('DATABASE_URI')).replace('postgresql+asyncpg:', 'postgresql:'))
    try:
        rows = await db.fetch("SELECT e.id,e.display_name,e.total_test_cases,e.total_test_runs,e.custom_config FROM api_endpoints e JOIN projects p ON p.id=e.project_id WHERE p.identifier=$1 AND e.display_name LIKE '%memLogout%'", 'PR-9')
        for r in rows:
            cfg = json.loads(r['custom_config'] or '{}')
            print(json.dumps({k:str(r[k]) for k in ['id','display_name','total_test_cases','total_test_runs']},ensure_ascii=True))
            print('config_keys',list(cfg))
            a = await db.fetch('SELECT file_name,object_name,entity_type,created_at FROM attachments WHERE entity_id=$1', r['id'])
            print(json.dumps([dict(x) for x in a],default=str,ensure_ascii=True))
    finally:
        await db.close()

asyncio.run(main())
